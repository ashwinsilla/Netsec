#!/usr/bin/env python3
"""
AVD structured-output generation (syntax path): OpenRouter calls, Anchor & Threat
prompting, <thinking> strip + json.loads, sub-tree merge, artifact layout under results/.
API-level response_format is NOT used (prompt-enforced JSON only).

Usage:
  export OPENROUTER_API_KEY=...   # or copy env.txt to .env and source if you prefer
  python3 generate_configs.py [--model MODEL] [--task-id P01]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import yaml

from avd_tasks import all_tasks, task_by_id
from subtree_merge import merge_yaml_file

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = REPO_ROOT / "results"
SCHEMA_PATH = REPO_ROOT / "phase 1" / "eos_designs.schema.yml"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    "openai/gpt-5.4",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-sonnet-4.6",
]

MAX_RETRIES = 3
RETRY_DELAY = 5
REQUEST_TIMEOUT = 120
CONCURRENCY = 4

SYSTEM_PROMPT = (
    "You are a headless network configuration engine. Your sole output modality is raw, parseable JSON. "
    "You will receive an AVD schema and specific configuration requirements. "
    "CRITICAL CONSTRAINT: Your entire final output must be strictly parseable by Python `json.loads()`. "
    "Do NOT wrap the output in markdown fences. Do NOT include conversational text. "
    "The very first character of your JSON output MUST be `{` or `[` and the last character MUST be `}` or `]`."
)

THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)

log = logging.getLogger(__name__)


def model_dirname(model: str) -> str:
    return model.replace("/", "-")


def load_schema_text() -> str:
    if not SCHEMA_PATH.exists():
        log.warning("Schema file missing at %s — user prompt will omit schema block.", SCHEMA_PATH)
        return ""
    return SCHEMA_PATH.read_text(encoding="utf-8", errors="replace")


def build_user_message(schema_text: str, task_text: str, context_yaml: str) -> str:
    schema_block = (
        f"<schema_reference>\n{schema_text}\n</schema_reference>\n\n" if schema_text else ""
    )
    return (
        f"{schema_block}"
        f"[Insert YAML/Schema Context Here]\n\n"
        f"{context_yaml}\n\n"
        f"Task: {task_text}\n\n"
        "Before generating the JSON, open a <thinking> block to map the user's request to the exact schema "
        "field names, verify the required nesting layers, and resolve any conditional defaults. "
        "After closing your </thinking> block, output the JSON.\n\n"
        "Subtree rule: output ONLY the JSON value that should replace the target subtree at the path given "
        "in the manifest (list-valued targets must include the full merged list after your edits). "
        "If the target is a scalar or a single object field, output that JSON type directly.\n\n"
        "Remember: Output ONLY the raw JSON object or array immediately after the </thinking> tag. "
        "Any non-JSON text will cause a fatal syntax error."
    )


def strip_thinking(raw: str) -> str:
    return THINKING_BLOCK.sub("", raw).strip()


def extract_json_value(text: str):
    """Parse JSON from text after thinking removal; tolerate leading noise."""
    s = text.strip()
    if not s:
        raise ValueError("empty string")
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    start_idx = None
    for i, ch in enumerate(s):
        if ch in "[{":
            start_idx = i
            break
    if start_idx is None:
        raise ValueError("no JSON start `{` or `[` found")

    stack: list[str] = []
    pairs = {"{": "}", "[": "]"}
    for j in range(start_idx, len(s)):
        ch = s[j]
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                raise ValueError("unbalanced closing bracket")
            op = stack.pop()
            if pairs[op] != ch:
                raise ValueError("mismatched bracket")
            if not stack:
                return json.loads(s[start_idx : j + 1])
    raise ValueError("unclosed JSON")


async def call_openrouter(
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    user_message: str,
) -> tuple[str | None, str | None]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cmu.edu/18662",
        "X-Title": "18-662-AVD-Generate",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.0,
        "max_tokens": 8192,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status == 429:
                    wait = RETRY_DELAY * attempt
                    log.warning("Rate limited %s, sleep %ss", model, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    body = await resp.text()
                    return None, f"HTTP {resp.status}: {body[:500]}"
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                return raw, None
        except asyncio.TimeoutError:
            log.warning("Timeout %s attempt %s", model, attempt)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            return None, str(e)
    return None, f"failed after {MAX_RETRIES} retries"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def run_one_job(
    session: aiohttp.ClientSession,
    api_key: str,
    schema_text: str,
    task: dict,
    model: str,
    repo_root: Path,
    semaphore: asyncio.Semaphore,
) -> None:
    out_dir = (
        RESULTS_DIR
        / model_dirname(model)
        / task["quadrant_dir"]
        / task["task_slug"]
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    context_path = repo_root / task["context_file"]
    context_yaml = context_path.read_text(encoding="utf-8")
    user_msg = build_user_message(schema_text, task["task_text"], context_yaml)

    manifest: dict = {
        "task_id": task["id"],
        "quadrant": task["quadrant_dir"],
        "task_slug": task["task_slug"],
        "model": model,
        "context_file": task["context_file"],
        "insertion_path": task["insertion_path"],
        "syntax_ok": False,
        "merge_ok": False,
        "api_error": None,
        "syntax_error": None,
        "merge_error": None,
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    async with semaphore:
        raw, err = await call_openrouter(session, api_key, model, user_msg)

    write_text(out_dir / "raw_response.txt", raw or "")

    if err:
        manifest["api_error"] = err
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("%s %s — API error", task["id"], model)
        return

    try:
        after_think = strip_thinking(raw)
        parsed = extract_json_value(after_think)
        manifest["syntax_ok"] = True
        (out_dir / "parsed.json").write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except Exception as e:
        manifest["syntax_error"] = str(e)[:800]
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("%s %s — syntax fail: %s", task["id"], model, e)
        return

    try:
        merged_yaml = merge_yaml_file(repo_root, task["context_file"], task["insertion_path"], parsed)
        write_text(out_dir / "config.yaml", merged_yaml)
        manifest["merge_ok"] = True
    except Exception as e:
        manifest["merge_error"] = str(e)[:800]
        log.info("%s %s — merge fail: %s", task["id"], model, e)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("%s %s — done syntax=%s merge=%s", task["id"], model, manifest["syntax_ok"], manifest["merge_ok"])


async def async_main(args: argparse.Namespace) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set. See env.txt.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    schema_text = load_schema_text()
    tasks = all_tasks()
    if args.task_id:
        t = task_by_id(args.task_id)
        if not t:
            sys.exit(f"Unknown task id: {args.task_id}")
        tasks = [t]

    models = [args.model] if args.model else MODELS

    jobs: list[tuple[dict, str]] = [(t, m) for m in models for t in tasks]
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=max(CONCURRENCY * 3, 8))

    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(
            *[
                run_one_job(session, api_key, schema_text, task, model, REPO_ROOT, sem)
                for task, model in jobs
            ]
        )

    log.info("Finished %s generation jobs.", len(jobs))


def main() -> None:
    p = argparse.ArgumentParser(description="Generate merged AVD configs via OpenRouter (prompt JSON only).")
    p.add_argument("--model", help="Run a single OpenRouter model id (default: all three frontier models).")
    p.add_argument("--task-id", help="Run a single task id, e.g. P01 (default: all 30).")
    args = p.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
