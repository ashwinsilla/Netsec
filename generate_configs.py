#!/usr/bin/env python3
"""
AVD structured-output generation (syntax path): OpenRouter calls, Anchor & Threat
prompting, JSON parse + sub-tree merge, artifact layout under results/.
API-level response_format is NOT used (prompt-enforced JSON only).

Text artifacts (exactly two .txt files when the HTTP call succeeds):
  raw_response.txt    — verbatim API message.content (unchanged from the provider).
  json_candidate.txt  — exact substring passed to json.loads on success; on parse failure, a short debug dump.
  parsed.json         — parsed JSON value (syntax success only).
  config.yaml         — merged full group_vars file (merge success only).
  manifest.json       — metadata including artifacts[] and json_parse_source.

If the HTTP call fails, only raw_response.txt is written (no json_candidate).

Usage:
  Put OPENROUTER_API_KEY in a .env file at the repo root (see env.txt), or export it in the shell.
  python3 generate_configs.py [--model MODEL] [--task-id P01] [--report-dir DIR]

  ``--report-dir`` is a folder under ``experiments/`` (relative to repo root unless absolute); each run
  writes a timestamped ``generate_configs_<UTC>.txt`` log mirroring the terminal (line-flushed as it runs).
  Omit ``--report-dir`` to use ``experiments/auto_<UTC>/`` automatically.
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
from dotenv import load_dotenv

from avd_tasks import all_tasks, task_by_id
from experiment_report import configure_run_logging
from subtree_merge import merge_yaml_file

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = REPO_ROOT / "results"
SCHEMA_PATH = REPO_ROOT / "phase 1" / "eos_designs.schema.yml"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    "openai/gpt-4o-mini",
    "google/gemini-3.1-flash-lite-preview"
    # "openai/gpt-5.4",
    # "google/gemini-3.1-pro-preview",
    # "anthropic/claude-sonnet-4.6",
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
THINKING_INNER = re.compile(r"<thinking>(.*?)</thinking>", re.IGNORECASE | re.DOTALL)

# Max chars written into json_candidate.txt on parse failure (debug sections).
_JSON_CANDIDATE_DEBUG_CAP = 12_000

log = logging.getLogger(__name__)


def _normalize_openrouter_api_key(raw: str) -> str:
    """Strip whitespace/BOM, optional quotes, accidental 'Bearer ' prefix."""
    s = raw.strip().strip("\ufeff")
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s


def load_openrouter_api_key() -> str:
    """
    Load from repo-root .env first (override=True so a bad shell export does not
    mask the key in .env). Accept OPENROUTER_API_KEY or OPEN_ROUTER_API_KEY.
    """
    env_path = REPO_ROOT / ".env"
    load_dotenv(env_path, override=True)
    for var in ("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"):
        raw = os.environ.get(var)
        if raw:
            key = _normalize_openrouter_api_key(raw)
            if key:
                return key
    return ""


def model_dirname(model: str) -> str:
    return model.replace("/", "-")


def load_schema_text() -> str:
    if not SCHEMA_PATH.exists():
        log.warning("Schema file missing at %s — user prompt will omit schema block.", SCHEMA_PATH)
        return ""
    return SCHEMA_PATH.read_text(encoding="utf-8", errors="replace")


def build_user_message(
    schema_text: str,
    task_text: str,
    context_yaml: str,
    context_file: str,
    insertion_path: list,
) -> str:
    schema_block = (
        f"<schema_reference>\n{schema_text}\n</schema_reference>\n\n" if schema_text else ""
    )
    path_s = json.dumps(insertion_path, separators=(",", ":"))
    merge_block = (
        "<merge_target>\n"
        f"context_file: {context_file}\n"
        f"insertion_path: {path_s}\n"
        "The tool will walk the YAML root following insertion_path (string keys = dict keys, "
        "integers = list indices) and replace exactly one value with your JSON.\n"
        "Shape rules: (1) If insertion_path is a single string key [\"K\"], your JSON root must NOT "
        "repeat \"K\" — e.g. [\"l3leaf\"] means an object with keys like defaults and node_groups only; "
        "[\"tenants\"] or [\"servers\"] means a JSON array at the root; [\"ntp_settings\"] means the "
        "ntp_settings object alone. "
        "(2) Do not echo the file-level key `type:` or other keys outside the replaced subtree.\n"
        "Root JSON must be a single object `{...}` or array `[...]` (first non-whitespace character `{` or `[`), "
        "matching the system constraint.\n"
        "</merge_target>\n\n"
    )
    context_block = (
        f"<configuration_context source=\"{context_file}\">\n"
        f"{context_yaml.rstrip()}\n"
        "</configuration_context>\n\n"
    )
    task_block = f"<task>\n{task_text}\n</task>\n\n"
    return (
        f"{schema_block}"
        f"{merge_block}"
        f"{context_block}"
        f"{task_block}"
        "Before generating the JSON, open a <thinking> block to map the user's request to the exact schema "
        "field names, verify the required nesting layers, and resolve any conditional defaults. "
        "After closing your </thinking> block, output the JSON.\n\n"
        "Subtree rule: output ONLY the JSON object or array that replaces the merge target above "
        "(for list-valued merge targets, include the full merged array after your edits).\n\n"
        "Remember: Output ONLY the raw JSON object or array immediately after the </thinking> tag. "
        "Any non-JSON text will cause a fatal syntax error."
    )


def strip_thinking_blocks(raw: str) -> str:
    """Remove all <thinking>...</thinking> regions (non-greedy)."""
    return THINKING_BLOCK.sub("", raw).strip()


def extract_thinking_inner(raw: str) -> str | None:
    """Return inner text of the first <thinking> block, or None if absent."""
    m = THINKING_INNER.search(raw)
    return m.group(1).strip() if m else None


def extract_json_value_with_source(text: str) -> tuple[object, str]:
    """
    Parse one JSON value from text; tolerate leading/trailing noise via bracket scan.
    Returns (parsed_python_value, exact_json_substring_used).
    """
    s = text.strip()
    if not s:
        raise ValueError("empty string")
    try:
        return json.loads(s), s
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
                snippet = s[start_idx : j + 1]
                return json.loads(snippet), snippet
    raise ValueError("unclosed JSON")


def resolve_parsed_json(raw: str) -> tuple[object, str, str]:
    """
    Prefer JSON in the post-</thinking> tail; if that fails or is empty, try the
    interior of the first <thinking> block (models often misplace JSON there).

    Returns (parsed_obj, json_substring_used, source) where source is
    'post_thinking' or 'thinking_interior'.
    """
    post = strip_thinking_blocks(raw)
    if post:
        try:
            obj, src = extract_json_value_with_source(post)
            return obj, src, "post_thinking"
        except (json.JSONDecodeError, ValueError):
            pass

    inner = extract_thinking_inner(raw)
    if inner:
        try:
            obj, src = extract_json_value_with_source(inner)
            return obj, src, "thinking_interior"
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"post_thinking and thinking_interior parse failed: {e}") from e
    raise ValueError("no JSON found (empty after stripping thinking, and no <thinking> inner text)")


def format_json_candidate_debug(post_tail: str, thinking_inner: str | None, err: str) -> str:
    """When parse fails, explain what was tried (trimmed)."""
    lines = [
        "# JSON parse failed. Sections below are what the harness attempted to parse.",
        f"# syntax_error: {err[:500]}",
        "",
        "## post_thinking (content after removing <thinking>...</thinking>)",
        (post_tail or "(empty)")[:_JSON_CANDIDATE_DEBUG_CAP],
        "",
        "## thinking_interior (first <thinking> block body only)",
        ((thinking_inner or "(no <thinking> block)"))[:_JSON_CANDIDATE_DEBUG_CAP],
        "",
    ]
    return "\n".join(lines)


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
        "max_tokens": 8000,
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
                    msg = f"HTTP {resp.status}: {body[:500]}"
                    if resp.status == 401:
                        msg += (
                            " | OpenRouter auth failed: key invalid/revoked, or masked by a bad shell export. "
                            "Regenerate at https://openrouter.ai/settings/keys — root .env overrides shell (override=True)."
                        )
                    return None, msg
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
    user_msg = build_user_message(
        schema_text,
        task["task_text"],
        context_yaml,
        task["context_file"],
        task["insertion_path"],
    )

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
        "json_parse_source": None,
        "artifacts": [],
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }
    artifacts: list[str] = []

    async with semaphore:
        raw, err = await call_openrouter(session, api_key, model, user_msg)

    text = raw or ""
    write_text(out_dir / "raw_response.txt", text)
    artifacts.append("raw_response.txt")

    thinking_inner = extract_thinking_inner(text)

    if err:
        manifest["api_error"] = err
        manifest["artifacts"] = artifacts
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.warning("%s %s — API error: %s", task["id"], model, err[:400])
        return

    post_tail = strip_thinking_blocks(text)
    try:
        parsed, json_candidate, parse_source = resolve_parsed_json(text)
        manifest["syntax_ok"] = True
        manifest["json_parse_source"] = parse_source
        write_text(out_dir / "json_candidate.txt", json_candidate)
        artifacts.append("json_candidate.txt")
        (out_dir / "parsed.json").write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        artifacts.append("parsed.json")
    except Exception as e:
        err_s = str(e)
        manifest["syntax_error"] = err_s[:800]
        dbg = format_json_candidate_debug(post_tail, thinking_inner, err_s)
        write_text(out_dir / "json_candidate.txt", dbg)
        artifacts.append("json_candidate.txt")
        manifest["artifacts"] = artifacts
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("%s %s — syntax fail: %s", task["id"], model, e)
        return

    try:
        merged_yaml = merge_yaml_file(repo_root, task["context_file"], task["insertion_path"], parsed)
        write_text(out_dir / "config.yaml", merged_yaml)
        manifest["merge_ok"] = True
        artifacts.append("config.yaml")
    except Exception as e:
        manifest["merge_error"] = str(e)[:800]
        log.info("%s %s — merge fail: %s", task["id"], model, e)

    manifest["artifacts"] = artifacts
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("%s %s — done syntax=%s merge=%s", task["id"], model, manifest["syntax_ok"], manifest["merge_ok"])


async def async_main(args: argparse.Namespace) -> None:
    api_key = load_openrouter_api_key()
    if not api_key:
        sys.exit(
            "OPENROUTER_API_KEY is not set or empty after loading .env at repo root. "
            "Add OPENROUTER_API_KEY=sk-or-v1-... to .env (see env.txt). "
            "Alias OPEN_ROUTER_API_KEY is also accepted."
        )

    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        log.info("Loaded API key from %s (shell value overridden if present).", env_path)
    else:
        log.warning("No %s — using OPENROUTER_API_KEY from environment only.", env_path)
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
    p.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Experiment folder for this run's log (relative to experiments/ unless absolute). "
        "Default: experiments/auto_<UTC>/. A line-flushed generate_configs_<UTC>.txt mirrors stderr.",
    )
    args = p.parse_args()

    report_path = configure_run_logging(
        repo_root=REPO_ROOT,
        report_dir=args.report_dir,
        console_level=logging.INFO,
        log_format="%(asctime)s  %(message)s",
        report_basename="generate_configs",
    )
    log.info("Run report (continuous): %s", report_path)

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
