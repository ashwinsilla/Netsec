#!/usr/bin/env python3
"""
avd_agent.py — Natural-language AVD configuration agent.

Describe a change in plain English. The agent resolves which file and path to
modify, generates the JSON subtree via LLM, validates it with ansible-playbook
build.yml, and retries automatically on failure — feeding exact error messages
back to the model each time.

Usage
─────
  python3 avd_agent.py                             # interactive prompt
  python3 avd_agent.py "Add NTP server 2.pool.ntp.org"
  python3 avd_agent.py --dry-run "Change BGP ASN for spines to 65200"
  python3 avd_agent.py --intent-only "Add VRF VRF20 with one SVI"
  python3 avd_agent.py --model openai/gpt-5.4 "Change spanning tree mode to rstp"
  python3 avd_agent.py -y "Add a second DNS server 8.8.8.8"

Flags
─────
  --dry-run       Generate and validate, but do NOT permanently apply the change.
  --intent-only   Preview which file/path the agent maps to, then stop.
  --model MODEL   OpenRouter model ID (default: anthropic/claude-sonnet-4.6).
  -y / --yes      Skip the confirmation prompt after intent resolution.
  -v / --verbose  DEBUG logging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from subtree_merge import merge_yaml_file

# ──────────────────────────────────────────────────────────────────────────────
# Paths and constants
# ──────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent

# group_vars files the agent can edit
FILE_MAP: dict[str, str] = {
    "fabric":    "group_vars/FABRIC/fabric_variables.yml",
    "spines":    "group_vars/DC1_SPINES/spines.yml",
    "l3leaves":  "group_vars/DC1_L3_LEAVES/l3_leaves.yml",
    "netsvcs":   "group_vars/NETWORK_SERVICES/network_services.yml",
    "endpoints": "group_vars/CONNECTED_ENDPOINTS/connected_endpoints.yml",
}

FILE_PURPOSES: dict[str, str] = {
    "fabric": (
        "Fabric-wide settings: NTP (ntp_settings), DNS (dns_settings), BGP peer groups "
        "(bgp_peer_groups), AAA (aaa_settings), p2p_uplinks_mtu, timezone."
    ),
    "spines": (
        "Spine nodes (spine.nodes) and spine defaults: BGP ASN (spine.defaults.bgp_as), "
        "platform, loopback pools."
    ),
    "l3leaves": (
        "L3 leaf node groups (l3leaf.node_groups) and defaults (l3leaf.defaults): "
        "virtual_router_mac_address, spanning_tree_mode/priority, uplink settings, MLAG."
    ),
    "netsvcs": (
        "Network services: tenants list, VRFs, VLANs, SVIs, IP helpers, L2 VLANs."
    ),
    "endpoints": (
        "Connected endpoints: servers list with adapter definitions."
    ),
}

OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL   = "anthropic/claude-sonnet-4.6"
MAX_RETRIES     = 3      # generation + validation attempts per job
API_TIMEOUT     = 180    # seconds per LLM call
HTTP_RETRIES    = 3      # retries on transient HTTP errors
RUNS_DIR        = REPO_ROOT / "agent_runs"

AVD_SCHEMA_PICKLE = (
    REPO_ROOT / ".venv/lib/python3.11/site-packages"
    "/pyavd/_eos_designs/schema/eos_designs.schema.pickle"
)
SCHEMA_TEXT_PATH = REPO_ROOT / "phase 1" / "eos_designs.schema.yml"

_THINK_STRIP = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)
_THINK_INNER = re.compile(r"<thinking>(.*?)</thinking>", re.IGNORECASE | re.DOTALL)
_MD_FENCE    = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Terminal helpers
# ──────────────────────────────────────────────────────────────────────────────

_IS_TTY = sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _IS_TTY else ""


BOLD   = _c("\033[1m")
GREEN  = _c("\033[32m")
RED    = _c("\033[31m")
CYAN   = _c("\033[36m")
YELLOW = _c("\033[33m")
DIM    = _c("\033[2m")
RESET  = _c("\033[0m")


def _print_step(label: str, detail: str = "") -> None:
    d = f"  {DIM}{detail}{RESET}" if detail else ""
    print(f"  {CYAN}▶{RESET} {label}{d}")


def _print_ok(msg: str)   -> None: print(f"  {GREEN}✓{RESET} {msg}")
def _print_fail(msg: str) -> None: print(f"  {RED}✗{RESET} {msg}")
def _print_warn(msg: str) -> None: print(f"  {YELLOW}!{RESET} {msg}")
def _print_info(msg: str) -> None: print(f"    {DIM}{msg}{RESET}")


def _banner(title: str) -> None:
    print(f"\n{CYAN}{BOLD}{'─' * 52}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'─' * 52}{RESET}")


# ──────────────────────────────────────────────────────────────────────────────
# API key
# ──────────────────────────────────────────────────────────────────────────────

def _load_api_key() -> str:
    load_dotenv(REPO_ROOT / ".env", override=True)
    for var in ("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"):
        raw = os.environ.get(var, "").strip().strip("\ufeff")
        if raw:
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                raw = raw[1:-1].strip()
            if raw.lower().startswith("bearer "):
                raw = raw[7:].strip()
            if raw:
                return raw
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Schema helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_avd_schema() -> dict:
    if AVD_SCHEMA_PICKLE.exists():
        with open(AVD_SCHEMA_PICKLE, "rb") as fh:
            return pickle.load(fh)
    return {}


def _load_schema_text() -> str:
    if SCHEMA_TEXT_PATH.exists():
        return SCHEMA_TEXT_PATH.read_text(encoding="utf-8", errors="replace")
    return ""


def _walk_schema(schema: dict, path: list) -> dict | None:
    node = schema
    for seg in path:
        if not isinstance(node, dict):
            return None
        node = node.get("items", {}) if isinstance(seg, int) else (node.get("keys") or {}).get(seg)
        if node is None:
            return None
    return node


def _schema_hint(schema: dict, insertion_path: list) -> str:
    """
    Return a short schema constraint block for the given insertion_path.
    Shows valid keys, types, and required fields — enough for the model to
    avoid hallucinating invalid keys.
    """
    node = _walk_schema(schema, insertion_path)
    if not node:
        return ""

    def fmt(n: dict, depth: int = 0) -> list[str]:
        if not isinstance(n, dict):
            return []
        ind = "  " * depth
        lines: list[str] = []
        ntype = n.get("type", "?")
        desc  = (n.get("description") or "").split("\n")[0][:100]
        lines.append(f"{ind}type: {ntype}" + (f"  # {desc}" if desc else ""))
        if n.get("valid_values"):
            lines.append(f"{ind}valid_values: {n['valid_values']}")
        if ntype == "dict" and depth < 2:
            keys = n.get("keys", {})
            req  = [k for k, v in keys.items() if isinstance(v, dict) and v.get("required")]
            opt  = [k for k in keys if k not in req]
            if req: lines.append(f"{ind}required_keys: {req}")
            if opt: lines.append(f"{ind}optional_keys:  {opt}")
        elif ntype == "list" and depth < 2:
            items = n.get("items", {})
            if items:
                lines.append(f"{ind}items:")
                lines.extend(fmt(items, depth + 1))
        return lines

    path_str = ".".join(str(s) for s in insertion_path)
    body = "\n".join(fmt(node))
    return (
        f"AVD schema for '{path_str}':\n{body}\n"
        "Keys not listed above will cause Ansible to fail with 'Invalid key'."
    )


# ──────────────────────────────────────────────────────────────────────────────
# OpenRouter API
# ──────────────────────────────────────────────────────────────────────────────

async def _call_llm(
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    system: str,
    user: str,
) -> tuple[str | None, str | None]:
    """Returns (content, error_msg) — exactly one is None."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cmu.edu/avd-agent",
        "X-Title": "AVD-Agent",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 8000,
    }
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            async with session.post(
                OPENROUTER_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(5 * attempt)
                    continue
                if resp.status != 200:
                    body = await resp.text()
                    return None, f"HTTP {resp.status}: {body[:300]}"
                data    = await resp.json()
                content = data["choices"][0]["message"]["content"]
                if content is None:
                    tok = data.get("usage", {}).get("completion_tokens", 0)
                    return None, f"Model returned null content (completion_tokens={tok})"
                return content, None
        except asyncio.TimeoutError:
            if attempt < HTTP_RETRIES:
                await asyncio.sleep(5)
        except Exception as exc:
            return None, str(exc)
    return None, "API call failed after all retries"


# ──────────────────────────────────────────────────────────────────────────────
# JSON extraction
# ──────────────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> tuple[object, str]:
    """
    Parse the first valid JSON value from text.
    Strips <thinking> blocks and markdown fences before attempting.
    Returns (parsed_value, json_string_used).
    """
    # 1. Remove thinking blocks
    stripped = _THINK_STRIP.sub("", text).strip()

    # 2. Strip markdown fences (```json ... ``` or ``` ... ```)
    fenced_match = _MD_FENCE.search(stripped)
    candidates = [
        _MD_FENCE.sub(r"\1", stripped).strip(),  # text with fences removed
        stripped,                                  # original (no fence)
    ]
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    for candidate in candidates:
        if not candidate:
            continue
        # Direct parse (handles scalars, objects, arrays)
        try:
            return json.loads(candidate), candidate
        except json.JSONDecodeError:
            pass
        # Bracket scan for objects/arrays embedded in prose
        start = next((i for i, c in enumerate(candidate) if c in "{["), None)
        if start is not None:
            stack: list[str] = []
            pairs = {"{": "}", "[": "]"}
            for j in range(start, len(candidate)):
                ch = candidate[j]
                if ch in "{[":
                    stack.append(ch)
                elif ch in "}]" and stack:
                    if pairs[stack.pop()] != ch:
                        break
                    if not stack:
                        snip = candidate[start: j + 1]
                        try:
                            return json.loads(snip), snip
                        except json.JSONDecodeError:
                            break

    # Last resort: try thinking interior
    inner_match = _THINK_INNER.search(text)
    if inner_match:
        inner = inner_match.group(1).strip()
        try:
            return json.loads(inner), inner
        except json.JSONDecodeError:
            pass

    raise ValueError("no valid JSON found in model response")


# ──────────────────────────────────────────────────────────────────────────────
# Ansible
# ──────────────────────────────────────────────────────────────────────────────

_ENV_DROP = ("ANSIBLE_INVENTORY", "ANSIBLE_INVENTORY_FILE")


def _ansible_env() -> dict:
    env = os.environ.copy()
    for k in _ENV_DROP:
        env.pop(k, None)
    vbin = (REPO_ROOT / ".venv" / "bin").resolve()
    if vbin.is_dir():
        prefix = str(vbin) + os.pathsep
        if not env.get("PATH", "").startswith(prefix):
            env["PATH"] = prefix + env.get("PATH", "")
    cfg = REPO_ROOT / "ansible.cfg"
    if cfg.is_file():
        env["ANSIBLE_CONFIG"] = str(cfg)
    else:
        env.pop("ANSIBLE_CONFIG", None)
    return env


def _run_ansible() -> tuple[bool, str]:
    cmd = [
        "ansible-playbook",
        str(REPO_ROOT / "build.yml"),
        "-i", str(REPO_ROOT / "inventory.yml"),
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), env=_ansible_env(),
            capture_output=True, text=True, timeout=300,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        log.debug("ansible rc=%s elapsed=%.1fs", proc.returncode, time.perf_counter() - t0)
        return proc.returncode == 0, combined[-8000:]
    except subprocess.TimeoutExpired:
        return False, "ansible-playbook timed out (300 s)"
    except FileNotFoundError:
        return False, "ansible-playbook not found — activate .venv"
    except Exception as exc:
        return False, str(exc)


def _extract_errors(output: str, limit: int = 30) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if ("[ERROR]:" in s or s.startswith("fatal:")) and s not in seen:
            seen.add(s); out.append(s)
            if len(out) >= limit:
                break
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 — Intent resolution
# ──────────────────────────────────────────────────────────────────────────────

_INTENT_SYSTEM = (
    "You are an expert Arista AVD configuration assistant. "
    "Given a user's natural-language change request and the current state of all "
    "group_vars files, identify exactly which file and insertion_path the change targets. "
    "Use a <thinking> block for your reasoning, then output ONLY a single raw JSON object — "
    "no markdown fences, no prose outside the thinking block."
)

_INTENT_OUTPUT_SPEC = """
Output a JSON object with exactly these fields:
{
  "file_key":       "<one of: fabric | spines | l3leaves | netsvcs | endpoints>",
  "context_file":   "<relative path, e.g. group_vars/FABRIC/fabric_variables.yml>",
  "insertion_path": [<list of string keys and 0-based integer indices>],
  "task_text":      "<precise, self-contained description of the change for the generation step>",
  "confidence":     "<high | medium | low>",
  "reasoning":      "<one sentence explaining the file/path choice>"
}

insertion_path rules
────────────────────
- String elements navigate dict keys.
- Integer elements navigate list indices (0-based), based on the current file content.
- The path points to the value being replaced or the list being appended to.

Examples
────────
  "Add NTP server"              → ["ntp_settings", "servers"]
  "Change spine BGP ASN"        → ["spine", "defaults", "bgp_as"]
  "Change virtual router MAC"   → ["l3leaf", "defaults", "virtual_router_mac_address"]
  "Add VRF to TENANT1"          → ["tenants", 0, "vrfs"]
  "Add SVI to VRF10 in TENANT1" → ["tenants", 0, "vrfs", 0, "svis"]
"""


def _build_intent_prompt(user_request: str) -> str:
    parts: list[str] = []

    # File catalogue
    parts.append("<available_files>")
    for key, rel in FILE_MAP.items():
        parts.append(f"  {key} ({rel}): {FILE_PURPOSES[key]}")
    parts.append("</available_files>\n")

    # Current content of all group_vars files
    parts.append("<current_group_vars>")
    for key, rel in FILE_MAP.items():
        path = REPO_ROOT / rel
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            parts.append(f"\n# {rel}\n{content.rstrip()}")
    parts.append("\n</current_group_vars>\n")

    parts.append(f"<user_request>\n{user_request.strip()}\n</user_request>\n")
    parts.append(_INTENT_OUTPUT_SPEC)
    return "\n".join(parts)


async def _resolve_intent(
    user_request: str,
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    work_dir: Path,
) -> dict | None:
    user_msg = _build_intent_prompt(user_request)
    (work_dir / "intent_prompt.txt").write_text(user_msg, encoding="utf-8")

    raw, err = await _call_llm(session, api_key, model, _INTENT_SYSTEM, user_msg)
    (work_dir / "intent_raw.txt").write_text(raw or "", encoding="utf-8")

    if err:
        log.error("Intent API error: %s", err)
        return None

    try:
        parsed, _ = _parse_json(raw)
        if not isinstance(parsed, dict):
            raise ValueError("response is not a JSON object")
        for field in ("file_key", "context_file", "insertion_path", "task_text"):
            if field not in parsed:
                raise ValueError(f"missing field: {field!r}")
        if parsed["file_key"] not in FILE_MAP:
            raise ValueError(f"unknown file_key: {parsed['file_key']!r}")
        (work_dir / "intent.json").write_text(
            json.dumps(parsed, indent=2) + "\n", encoding="utf-8"
        )
        return parsed
    except Exception as exc:
        log.error("Intent parse error: %s | raw: %s", exc, (raw or "")[:400])
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Generation prompt builder
# ──────────────────────────────────────────────────────────────────────────────

_GEN_SYSTEM = (
    "You are a headless network configuration engine. Your sole output modality is raw, parseable JSON. "
    "You will receive an AVD schema and specific configuration requirements. "
    "CRITICAL CONSTRAINT: Your entire final output must be strictly parseable by Python `json.loads()`. "
    "Do NOT wrap the output in markdown fences. Do NOT include conversational text. "
    "The very first character of your JSON output MUST be `{` or `[` and the last character MUST be `}` or `]`. "
    "SCALAR EXCEPTION: when the insertion_path targets a scalar field (string/int/bool), "
    "output the raw JSON scalar directly — e.g. `\"rstp\"` — without wrapping in `{...}`."
)


def _build_gen_prompt(
    intent: dict,
    context_yaml: str,
    schema_text: str,
    schema_hint: str,
    errors: list[str],
    attempt: int,
) -> str:
    path_s        = json.dumps(intent["insertion_path"], separators=(",", ":"))
    context_file  = intent["context_file"]
    parts: list[str] = []

    # Full AVD schema (same block as experiment1 — required by all models)
    if schema_text:
        parts.append(f"<schema_reference>\n{schema_text}\n</schema_reference>\n")

    # Merge target (verbatim experiment1 format — proven across all three models)
    parts.append(
        "<merge_target>\n"
        f"context_file: {context_file}\n"
        f"insertion_path: {path_s}\n"
        "The tool walks the YAML root following insertion_path and replaces exactly one value "
        "with your JSON output.\n"
        "Shape rules:\n"
        "  (1) scalar field  → output the raw JSON scalar value.\n"
        "  (2) dict field    → output a JSON object `{...}`.\n"
        "  (3) list field    → output a JSON array `[...]` with ALL existing items plus your additions.\n"
        "Do NOT echo the top-level file key or content outside the replaced subtree.\n"
        "</merge_target>\n"
    )

    # Current config context
    parts.append(
        f"<configuration_context source=\"{context_file}\">\n"
        f"{context_yaml.rstrip()}\n"
        "</configuration_context>\n"
    )

    # Task
    parts.append(f"<task>\n{intent['task_text']}\n</task>\n")

    # Correction block — only when there are errors to feed back
    if errors or schema_hint:
        correction: list[str] = []
        if errors:
            deduped   = list(dict.fromkeys(errors))
            err_lines = "\n".join(f"  {e}" for e in deduped[:20])
            correction.append(
                f"VALIDATION FAILED (attempt {attempt}) — your previous output caused these "
                f"Ansible AVD errors. You MUST fix every one of them:\n{err_lines}"
            )
        if schema_hint:
            correction.append(schema_hint)
        parts.append(
            "<correction_notes>\n"
            + "\n\n".join(correction)
            + "\n</correction_notes>\n"
        )

    # Terminal anchor (exact wording proven in experiment1)
    parts.append(
        "Before generating the JSON, open a <thinking> block to map the request to exact "
        "schema field names, verify required nesting layers, and check all keys are valid. "
        "After closing your </thinking> block, output the JSON.\n\n"
        "Subtree rule: output ONLY the JSON subtree that replaces the merge target "
        "(for list targets, include the full merged list after your edits).\n\n"
        "Remember: Output ONLY the raw JSON immediately after </thinking>. "
        "Any non-JSON text causes a fatal parse error."
    )
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 — Generation + validation loop
# ──────────────────────────────────────────────────────────────────────────────

async def _generate_and_validate(
    intent: dict,
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    schema: dict,
    schema_text: str,
    dry_run: bool,
    work_dir: Path,
) -> tuple[bool, str]:
    """
    Runs the generate → validate → feedback loop.

    Returns (success, summary_message).
    On success with dry_run=False  → target group_vars file is updated in-place.
    On success with dry_run=True   → baseline is restored; nothing is permanently changed.
    On failure                     → baseline is always restored.
    """
    context_file  = intent["context_file"]
    insertion_path = intent["insertion_path"]
    target         = (REPO_ROOT / context_file).resolve()

    if not target.is_file():
        return False, f"Target file not found: {context_file}"

    context_yaml = target.read_text(encoding="utf-8")
    hint         = _schema_hint(schema, insertion_path)
    backup       = target.with_suffix(target.suffix + ".agent_bak")

    errors: list[str] = []
    final_ok  = False
    final_msg = f"All {MAX_RETRIES} attempts failed."

    for attempt in range(1, MAX_RETRIES + 1):
        attempt_dir = work_dir / f"attempt_{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)

        _print_step(
            f"Attempt {attempt}/{MAX_RETRIES}  — generating with {model.split('/')[-1]} …",
            intent["task_text"][:72],
        )

        # Build prompt
        prompt = _build_gen_prompt(
            intent, context_yaml, schema_text, hint, errors, attempt
        )
        (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        # Call LLM
        raw, api_err = await _call_llm(session, api_key, model, _GEN_SYSTEM, prompt)
        (attempt_dir / "raw_response.txt").write_text(raw or "", encoding="utf-8")

        if api_err:
            _print_fail(f"API error: {api_err[:120]}")
            continue

        # Parse JSON
        try:
            parsed, json_str, = _parse_json(raw)[:2]
            (attempt_dir / "parsed.json").write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            _print_fail(f"JSON parse error: {exc}")
            continue

        # Merge into YAML
        try:
            merged = merge_yaml_file(REPO_ROOT, context_file, insertion_path, parsed)
            (attempt_dir / "config.yaml").write_text(merged, encoding="utf-8")
        except Exception as exc:
            _print_fail(f"YAML merge error: {exc}")
            continue

        # Patch target file and run ansible
        _print_step(f"Attempt {attempt}/{MAX_RETRIES}  — running ansible-playbook build.yml …")
        ok_build = False
        output   = ""
        try:
            shutil.copy2(target, backup)
            target.write_text(merged, encoding="utf-8")
            ok_build, output = _run_ansible()
        finally:
            # Always restore on failure so the next attempt starts from a clean baseline
            if not ok_build and backup.is_file():
                shutil.copy2(backup, target)

        (attempt_dir / "ansible_output.txt").write_text(output[-8000:], encoding="utf-8")
        new_errors = _extract_errors(output)

        if ok_build:
            _print_ok(f"Build passed on attempt {attempt}.")
            if dry_run:
                if backup.is_file():
                    shutil.copy2(backup, target)
                _print_info("Dry-run mode: baseline restored — change NOT permanently applied.")
            else:
                _print_info(f"Change written to {context_file}")
            final_ok  = True
            final_msg = f"Build passed on attempt {attempt}."
            break
        else:
            _print_fail(f"Build failed — {len(new_errors)} error(s):")
            for line in new_errors[:5]:
                _print_info(line)
            if attempt < MAX_RETRIES:
                _print_info(f"Feeding {len(new_errors)} error(s) back into the next attempt …")
            errors = new_errors  # carry errors forward for the next correction prompt

    # Clean up backup
    if backup.is_file():
        backup.unlink(missing_ok=True)

    if not final_ok:
        final_msg = (
            f"All {MAX_RETRIES} attempts failed.\n"
            + "\n".join(f"  {e}" for e in errors[:8])
        )
    return final_ok, final_msg


# ──────────────────────────────────────────────────────────────────────────────
# Top-level agent runner
# ──────────────────────────────────────────────────────────────────────────────

async def _run_agent(user_request: str, args: argparse.Namespace) -> bool:
    api_key = _load_api_key()
    if not api_key:
        print(
            f"{RED}Error:{RESET} OPENROUTER_API_KEY not set.\n"
            "Create a .env file at the repo root:  OPENROUTER_API_KEY=sk-or-v1-…",
            file=sys.stderr,
        )
        return False

    model    = args.model
    dry_run  = args.dry_run
    schema   = _load_avd_schema()
    sch_text = _load_schema_text()

    # Working directory for all artifacts of this run
    ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    work_dir = RUNS_DIR / ts
    work_dir.mkdir(parents=True, exist_ok=True)

    # ── Header ────────────────────────────────────────────────────────────
    print(f"\n{CYAN}{BOLD}  AVD Configuration Agent{RESET}")
    print(f"  {BOLD}Request :{RESET} {user_request}")
    print(f"  {BOLD}Model   :{RESET} {model}")
    if dry_run:
        print(f"  {YELLOW}{BOLD}Mode    : DRY RUN — changes will NOT be permanently applied{RESET}")
    print(f"  {DIM}Run dir : {work_dir}{RESET}")

    connector = aiohttp.TCPConnector(limit=4)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Phase 1: Intent resolution ─────────────────────────────────────
        _banner("Phase 1 — Understanding your request")
        _print_step("Mapping request to file and insertion path …")

        intent = await _resolve_intent(user_request, session, api_key, model, work_dir)

        if intent is None:
            _print_fail("Could not resolve intent. Try rephrasing or use --intent-only to debug.")
            return False

        _print_ok(f"Intent resolved  (confidence: {intent.get('confidence', '?')})")
        _print_info(f"File       : {intent['context_file']}")
        _print_info(f"Path       : {intent['insertion_path']}")
        _print_info(f"Task       : {intent['task_text']}")
        if intent.get("reasoning"):
            _print_info(f"Reasoning  : {intent['reasoning']}")

        # Confirmation (skipped with -y or --intent-only)
        if not args.yes and not args.intent_only:
            print()
            try:
                ans = input(f"  {YELLOW}Proceed with this interpretation? [Y/n]:{RESET} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Aborted.")
                return False
            if ans not in ("", "y", "yes"):
                print("  Aborted.")
                return False

        if args.intent_only:
            print(f"\n  {DIM}(--intent-only: stopping here){RESET}\n")
            return True

        # ── Phase 2: Generate + validate ──────────────────────────────────
        _banner("Phase 2 — Generating and validating")

        success, msg = await _generate_and_validate(
            intent, session, api_key, model,
            schema, sch_text, dry_run, work_dir,
        )

    # ── Final result ──────────────────────────────────────────────────────
    _banner("Result")
    if success:
        _print_ok(msg)
        if not dry_run:
            _print_ok(f"Your change is live in  {intent['context_file']}")
    else:
        _print_fail(msg)

    print(f"\n  {DIM}Artifacts saved to: {work_dir}{RESET}\n")
    return success


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="AVD natural-language configuration agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "request", nargs="?", default=None,
        help="Change description (omit for interactive prompt).",
    )
    p.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"OpenRouter model ID (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Validate but do NOT permanently apply the change.",
    )
    p.add_argument(
        "--intent-only", action="store_true",
        help="Stop after intent resolution — preview file/path mapping.",
    )
    p.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip the confirmation prompt.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable DEBUG logging.",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.request:
        user_request = args.request.strip()
    else:
        print(f"\n{BOLD}AVD Configuration Agent{RESET}")
        print(f"{DIM}Describe the change you want to make to the fabric.{RESET}\n")
        try:
            user_request = input("  What change do you need? ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)
        if not user_request:
            print("  No input. Exiting.")
            sys.exit(0)

    success = asyncio.run(_run_agent(user_request, args))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
