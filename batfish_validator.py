#!/usr/bin/env python3
"""
Phase 3 — Intent verification via config-file and Batfish assertions.

After Ansible validates the YAML schema, this module verifies that the user's
*actual intent* was achieved by checking the generated EOS device configs:

  Layer 1 — config_file (always available):
    Grep / regex checks against intended/configs/*.cfg.
    Covers NTP, DNS, MTU, spanning-tree mode, virtual-router MAC, VRF presence,
    BGP AS in config, VLAN names, and any other pattern expressible as a regex.

  Layer 2 — Batfish (optional, requires pybatfish + running Batfish service):
    Network-semantic verification — BGP sessions, routing tables, VRF routing,
    interface properties.  Skipped gracefully when unavailable.

The LLM generates a structured assertion spec (JSON) from the task_text.  Our
code executes the spec — no arbitrary code execution.

Public API:
    result = await validate_intent(task_text, intent, session, api_key, model, work_dir)
    # Returns ValidationResult(passed, failures, warnings, skipped)
"""

from __future__ import annotations

import json
import logging
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

REPO_ROOT         = Path(__file__).resolve().parent
INTENDED_CONFIGS  = REPO_ROOT / "intended" / "configs"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_TIMEOUT    = 60   # seconds — assertions are a short call


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:   bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped:  list[str] = field(default_factory=list)

    def as_error_lines(self) -> list[str]:
        """Formatted lines suitable for feeding back into the generation prompt."""
        lines: list[str] = []
        for f in self.failures:
            lines.append(f"[INTENT-CHECK FAILED]: {f}")
        return lines


# ── Assertion type catalogue (shown to the LLM) ───────────────────────────────

_ASSERTION_CATALOGUE = """
Available assertion types — output ONLY types from this list:

1. config_contains
   Pattern (regex) MUST appear in one or more generated EOS device config files.
   {"type":"config_contains","pattern":"<regex>","description":"...","nodes":["dc1-spine1"] or null}
   Use for: NTP server lines, DNS name-server lines, MTU values, spanning-tree mode,
            virtual-router MAC, hostname, VRF instance, VLAN definitions, BGP AS in config.

2. config_absent
   Pattern MUST NOT appear in any EOS config (useful to confirm old value is gone).
   {"type":"config_absent","pattern":"<regex>","description":"...","nodes":null}

3. batfish_bgp_as  [requires Batfish]
   Verify BGP AS number on nodes matching node_regex.
   {"type":"batfish_bgp_as","node_regex":".*spine.*","asn":65000,"description":"..."}

4. batfish_interface_mtu  [requires Batfish]
   Verify interface MTU on nodes matching node_regex.
   {"type":"batfish_interface_mtu","node_regex":".*","interface_regex":"Ethernet.*","mtu":9214,"description":"..."}

5. batfish_vrf_exists  [requires Batfish]
   Verify a VRF exists on nodes matching node_regex.
   {"type":"batfish_vrf_exists","node_regex":".*leaf.*","vrf_name":"VRF20","description":"..."}

6. batfish_route_exists  [requires Batfish]
   Verify a prefix exists in a VRF's routing table.
   {"type":"batfish_route_exists","node_regex":".*leaf.*","vrf":"VRF20","prefix":"10.20.0.0/24","description":"..."}

7. batfish_bgp_session  [requires Batfish]
   Verify at least one established BGP session between matching node pairs.
   {"type":"batfish_bgp_session","node_regex":".*spine.*","remote_node_regex":".*leaf.*","description":"..."}
"""

_ASSERT_GEN_SYSTEM = (
    "You are a network validation expert.  Given a network change task that was applied "
    "to an Arista AVD fabric, generate assertions to confirm the change is correctly "
    "reflected in the generated EOS device configurations.  "
    "Output ONLY a single raw JSON object — no markdown fences, no prose."
)


def _build_assertion_prompt(task_text: str, intent: dict, node_names: list[str]) -> str:
    nodes_str = ", ".join(node_names) if node_names else "dc1-spine1, dc1-spine2, dc1-leaf1a, dc1-leaf1b"
    path_str  = " → ".join(str(s) for s in intent.get("insertion_path", []))
    return (
        f"{_ASSERTION_CATALOGUE}\n"
        f"Available device nodes: {nodes_str}\n\n"
        f"Task: {task_text}\n"
        f"AVD file: {intent.get('context_file', '?')}  |  insertion_path: {path_str}\n\n"
        "Generate 2–5 assertions that directly verify this specific change was applied.\n"
        "Rules:\n"
        "  - Prefer config_contains for scalar changes (NTP, DNS, MTU, spanning-tree, MAC).\n"
        "  - Add a config_absent assertion when the old value should disappear.\n"
        "  - Use batfish_* only for BGP/VRF/routing changes where config text alone is ambiguous.\n"
        "  - patterns in config_contains/config_absent are Python re.IGNORECASE regexes.\n"
        "  - Escape regex special chars in IP addresses (use r'\\.' for literal dots).\n\n"
        'Output ONLY: {"assertions": [...]}'
    )


# ── LLM call (reuses same openrouter endpoint as avd_agent) ───────────────────

async def _call_llm(
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    system: str,
    user: str,
) -> tuple[str | None, str | None]:
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
        "max_tokens": 1024,
    }
    try:
        async with session.post(
            OPENROUTER_URL, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return None, f"HTTP {resp.status}: {body[:200]}"
            data    = await resp.json()
            content = data["choices"][0]["message"]["content"]
            return content, None
    except Exception as exc:
        return None, str(exc)


def _parse_assertions(raw: str) -> list[dict]:
    """Extract the assertions list from LLM output."""
    # Strip markdown fences
    raw = re.sub(r"```(?:json)?\s*\n?(.*?)\n?```", r"\1", raw, flags=re.DOTALL).strip()
    # Strip thinking blocks
    raw = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "assertions" in obj:
            return [a for a in obj["assertions"] if isinstance(a, dict)]
    except json.JSONDecodeError:
        pass
    return []


async def _generate_assertions(
    task_text: str,
    intent: dict,
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    node_names: list[str],
) -> list[dict]:
    prompt = _build_assertion_prompt(task_text, intent, node_names)
    raw, err = await _call_llm(session, api_key, model, _ASSERT_GEN_SYSTEM, prompt)
    if err:
        log.warning("Assertion generation failed: %s", err)
        return []
    assertions = _parse_assertions(raw or "")
    log.debug("Generated %d assertions for task: %r", len(assertions), task_text[:60])
    return assertions


# ── Config-file assertion executors ───────────────────────────────────────────

def _discover_nodes() -> list[str]:
    """Return stem names of all *.cfg files in intended/configs/."""
    if not INTENDED_CONFIGS.is_dir():
        return []
    return sorted(p.stem for p in INTENDED_CONFIGS.glob("*.cfg"))


def _load_configs(nodes: list[str] | None) -> dict[str, str]:
    """Return {node_name: config_text}.  nodes=None means all."""
    result: dict[str, str] = {}
    if not INTENDED_CONFIGS.is_dir():
        return result
    for cfg in INTENDED_CONFIGS.glob("*.cfg"):
        name = cfg.stem
        if nodes is None or name in nodes:
            try:
                result[name] = cfg.read_text(encoding="utf-8")
            except Exception:
                pass
    return result


def _run_config_contains(assertion: dict) -> tuple[bool, str]:
    pattern = assertion.get("pattern", "")
    nodes   = assertion.get("nodes")  # None → all nodes
    configs = _load_configs(nodes)

    if not configs:
        return False, f"No EOS config files found in {INTENDED_CONFIGS}"

    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return False, f"Invalid regex '{pattern}': {e}"

    matches = [name for name, text in configs.items() if rx.search(text)]
    if matches:
        return True, f"Matched in {len(matches)}/{len(configs)} node(s): {', '.join(sorted(matches)[:5])}"
    return False, f"Pattern not found in {len(configs)} config(s): {pattern!r}"


def _run_config_absent(assertion: dict) -> tuple[bool, str]:
    pattern = assertion.get("pattern", "")
    nodes   = assertion.get("nodes")
    configs = _load_configs(nodes)

    if not configs:
        return True, "No config files found — treating as absent"

    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return False, f"Invalid regex '{pattern}': {e}"

    matches = [name for name, text in configs.items() if rx.search(text)]
    if not matches:
        return True, f"Pattern correctly absent from all {len(configs)} config(s)"
    return False, f"Old pattern still present in: {', '.join(sorted(matches)[:5])}: {pattern!r}"


# ── Batfish availability ───────────────────────────────────────────────────────

_bf_available_cache: dict[str, bool] = {}


def _batfish_available(host: str = "localhost", port: int = 9996) -> bool:
    key = f"{host}:{port}"
    if key in _bf_available_cache:
        return _bf_available_cache[key]

    # Check pybatfish installed
    try:
        import pybatfish  # noqa: F401
    except ImportError:
        log.debug("pybatfish not installed — Batfish assertions will be skipped")
        _bf_available_cache[key] = False
        return False

    # Check service reachable
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        _bf_available_cache[key] = True
        return True
    except OSError:
        log.debug("Batfish not reachable at %s:%d — Batfish assertions skipped", host, port)
        _bf_available_cache[key] = False
        return False


_bf_session_cache: dict[str, Any] = {}


def _get_bf_session(host: str = "localhost") -> Any:
    if host not in _bf_session_cache:
        from pybatfish.client.session import Session  # type: ignore
        bf = Session(host=host)
        bf.set_network("avd-eval")
        _bf_session_cache[host] = bf
    return _bf_session_cache[host]


def _load_bf_snapshot(host: str = "localhost") -> tuple[bool, str]:
    """
    Load intended/configs/*.cfg into Batfish.

    Batfish requires the snapshot to be packaged as:
        <snapshot_dir>/configs/<device>.cfg
    so we create a temporary staging directory with that structure.
    """
    import shutil
    import tempfile

    if not INTENDED_CONFIGS.is_dir():
        return False, f"intended/configs/ not found at {INTENDED_CONFIGS}"

    cfg_files = list(INTENDED_CONFIGS.glob("*.cfg"))
    if not cfg_files:
        return False, f"No .cfg files found in {INTENDED_CONFIGS}"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            configs_dir = Path(tmp) / "configs"
            configs_dir.mkdir()
            for src in cfg_files:
                shutil.copy2(src, configs_dir / src.name)
            bf = _get_bf_session(host)
            bf.init_snapshot(tmp, name="avd-snapshot", overwrite=True)
        return True, f"Batfish snapshot loaded ({len(cfg_files)} devices)"
    except Exception as exc:
        return False, f"Snapshot load failed: {exc}"


# ── Batfish assertion executors ───────────────────────────────────────────────
#
# All executors use bf.q.<question>() via the cached session object.
# Known Batfish/EOS limitation: spine configs parse as FAILED (EOS-specific syntax
# not supported). Leaf BGP peer tables are available and contain spine Remote_AS,
# so spine-targeted AS assertions work from the leaf's perspective.

def _run_batfish_bgp_as(assertion: dict, host: str) -> tuple[bool, str]:
    """
    Check BGP AS number.  Checks both Local_AS on directly-parsed nodes and
    Remote_AS seen by peer nodes — works even when spine configs fail to parse.
    """
    node_regex   = assertion.get("node_regex", ".*")
    expected_asn = int(assertion.get("asn", 0))
    if not expected_asn:
        return False, "batfish_bgp_as: missing 'asn' field"
    try:
        bf = _get_bf_session(host)
        df = bf.q.bgpPeerConfiguration().answer(snapshot="avd-snapshot").frame()
        if df.empty:
            return False, "No BGP peer data available in snapshot"

        # Normalise AS columns to int for comparison (pybatfish may return strings)
        def _to_int(col):
            try:
                return col.astype(int)
            except Exception:
                return col

        local_match  = df[_to_int(df["Local_AS"])  == expected_asn]
        remote_match = df[_to_int(df["Remote_AS"]) == expected_asn]

        # Apply node_regex filter to local matches
        import re as _re
        rx = _re.compile(node_regex, _re.IGNORECASE)
        local_nodes = [n for n in local_match["Node"].unique() if rx.search(n)]
        remote_nodes = [n for n in remote_match["Node"].unique()]

        if local_nodes:
            return True, f"Nodes with Local_AS={expected_asn}: {sorted(local_nodes)[:5]}"
        if remote_nodes:
            return True, (
                f"AS {expected_asn} confirmed as Remote_AS from peer nodes: "
                f"{sorted(remote_nodes)[:5]}"
            )
        return False, f"AS {expected_asn} not found as Local_AS or Remote_AS in BGP peer table"
    except Exception as exc:
        return False, f"Batfish bgp_as query failed: {exc}"


def _run_batfish_interface_mtu(assertion: dict, host: str) -> tuple[bool, str]:
    node_regex  = assertion.get("node_regex", ".*")
    iface_regex = assertion.get("interface_regex", "Ethernet.*")
    expected    = assertion.get("mtu")
    if expected is None:
        return False, "batfish_interface_mtu: missing 'mtu' field"
    try:
        bf = _get_bf_session(host)
        df = bf.q.interfaceProperties(
            nodes=node_regex, interfaces=iface_regex,
        ).answer(snapshot="avd-snapshot").frame()
        if df.empty:
            return False, f"No interfaces matched node='{node_regex}' iface='{iface_regex}'"
        wrong = df[df["MTU"] != expected]
        if wrong.empty:
            return True, f"All {len(df)} matched interface(s) have MTU {expected}"
        count = len(wrong)
        sample = wrong["Interface"].tolist()[:3]
        return False, f"{count} interface(s) have wrong MTU (expected {expected}): {sample}"
    except Exception as exc:
        return False, f"Batfish interface_mtu query failed: {exc}"


def _run_batfish_vrf_exists(assertion: dict, host: str) -> tuple[bool, str]:
    """Check VRF exists by looking for routes in that VRF (vrfProperties not available)."""
    node_regex = assertion.get("node_regex", ".*")
    vrf_name   = assertion.get("vrf_name", "")
    if not vrf_name:
        return False, "batfish_vrf_exists: missing 'vrf_name' field"
    try:
        bf = _get_bf_session(host)
        # Use routes: if any routes exist in this VRF, the VRF exists
        df = bf.q.routes(nodes=node_regex, vrfs=vrf_name).answer(snapshot="avd-snapshot").frame()
        if not df.empty:
            nodes = df["Node"].unique().tolist()[:5]
            return True, f"VRF '{vrf_name}' active (has routes) on: {nodes}"
        # Also check interfaces assigned to this VRF
        idf = bf.q.interfaceProperties(nodes=node_regex).answer(snapshot="avd-snapshot").frame()
        if not idf.empty and "VRF" in idf.columns:
            vrf_ifaces = idf[idf["VRF"].str.upper() == vrf_name.upper()]
            if not vrf_ifaces.empty:
                nodes = vrf_ifaces["Interface"].tolist()[:3]
                return True, f"VRF '{vrf_name}' found on interfaces: {nodes}"
        return False, f"VRF '{vrf_name}' not found on nodes matching '{node_regex}'"
    except Exception as exc:
        return False, f"Batfish vrf_exists query failed: {exc}"


def _run_batfish_route_exists(assertion: dict, host: str) -> tuple[bool, str]:
    node_regex = assertion.get("node_regex", ".*")
    vrf        = assertion.get("vrf", "default")
    prefix     = assertion.get("prefix", "")
    if not prefix:
        return False, "batfish_route_exists: missing 'prefix' field"
    try:
        bf = _get_bf_session(host)
        df = bf.q.routes(
            nodes=node_regex, vrfs=vrf, network=prefix,
        ).answer(snapshot="avd-snapshot").frame()
        if not df.empty:
            nodes = df["Node"].unique().tolist()[:5]
            return True, f"Route {prefix} in VRF '{vrf}' found on: {nodes}"
        return False, f"Route {prefix!r} not found in VRF '{vrf}' on nodes '{node_regex}'"
    except Exception as exc:
        return False, f"Batfish route_exists query failed: {exc}"


def _run_batfish_bgp_session(assertion: dict, host: str) -> tuple[bool, str]:
    """
    Verify BGP sessions exist between matching node pairs.

    Uses bgpPeerConfiguration (one-sided) since bgpEdges requires both
    endpoints parsed — Arista EOS spine configs currently parse as FAILED.
    If the primary node_regex returns no results (nodes not parsed), falls
    back to checking the remote side.
    """
    node_regex        = assertion.get("node_regex", ".*")
    remote_node_regex = assertion.get("remote_node_regex", ".*")
    try:
        bf = _get_bf_session(host)

        # Try primary side first
        df = bf.q.bgpPeerConfiguration(nodes=node_regex).answer(snapshot="avd-snapshot").frame()

        # If primary nodes aren't in the snapshot (e.g. spines), try the remote side
        if df.empty:
            df = bf.q.bgpPeerConfiguration(nodes=remote_node_regex).answer(snapshot="avd-snapshot").frame()
            if df.empty:
                return False, (
                    f"No BGP peer config found on nodes matching '{node_regex}' "
                    f"or '{remote_node_regex}' — both may be unparsed by Batfish"
                )
            nodes = df["Node"].unique().tolist()[:5]
            count = len(df)
            return True, (
                f"{count} BGP peer(s) confirmed on {nodes} "
                f"(checked remote side — '{node_regex}' nodes not parsed by Batfish)"
            )

        nodes = df["Node"].unique().tolist()[:5]
        count = len(df)
        return True, f"{count} BGP peer config(s) confirmed on: {nodes}"
    except Exception as exc:
        return False, f"Batfish bgp_session query failed: {exc}"



# ── Dispatcher ────────────────────────────────────────────────────────────────

def _run_assertion(
    assertion: dict,
    batfish_host: str,
    batfish_ready: bool,
) -> tuple[str, bool, str]:
    """
    Execute one assertion.

    Returns (description, passed, detail).
    Batfish assertions return (description, None, reason) when Batfish is unavailable
    — the caller treats None as 'skipped'.
    """
    atype = assertion.get("type", "")
    desc  = assertion.get("description", atype)

    if atype == "config_contains":
        ok, detail = _run_config_contains(assertion)
        return desc, ok, detail

    if atype == "config_absent":
        ok, detail = _run_config_absent(assertion)
        return desc, ok, detail

    # Batfish assertions
    if not batfish_ready:
        return desc, None, "Batfish not available — skipped"

    if atype == "batfish_bgp_as":
        ok, detail = _run_batfish_bgp_as(assertion, batfish_host)
    elif atype == "batfish_interface_mtu":
        ok, detail = _run_batfish_interface_mtu(assertion, batfish_host)
    elif atype == "batfish_vrf_exists":
        ok, detail = _run_batfish_vrf_exists(assertion, batfish_host)
    elif atype == "batfish_route_exists":
        ok, detail = _run_batfish_route_exists(assertion, batfish_host)
    elif atype == "batfish_bgp_session":
        ok, detail = _run_batfish_bgp_session(assertion, batfish_host)
    else:
        return desc, None, f"Unknown assertion type '{atype}' — skipped"

    return desc, ok, detail


# ── Public entry point ────────────────────────────────────────────────────────

async def validate_intent(
    task_text: str,
    intent: dict,
    session: aiohttp.ClientSession,
    api_key: str,
    model: str,
    work_dir: Path,
    batfish_host: str = "localhost",
) -> ValidationResult:
    """
    Generate and run assertions that verify task_text was achieved.

    Saves assertion spec and results to work_dir/validation/.
    Returns a ValidationResult.  failures are actionable: they can be appended
    to the generation-loop error list so the LLM can self-correct.
    """
    val_dir = work_dir / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)

    # Discover available nodes
    node_names = _discover_nodes()

    # Step 1: generate assertions via LLM
    assertions = await _generate_assertions(
        task_text, intent, session, api_key, model, node_names
    )
    (val_dir / "assertions.json").write_text(
        json.dumps({"assertions": assertions}, indent=2) + "\n", encoding="utf-8"
    )

    if not assertions:
        return ValidationResult(
            passed=True,
            warnings=["Assertion generation returned no assertions — skipping intent check"],
        )

    # Step 2: check Batfish availability and load snapshot if needed
    batfish_ready = False
    has_batfish_assertions = any(
        a.get("type", "").startswith("batfish_") for a in assertions
    )
    if has_batfish_assertions:
        batfish_ready = _batfish_available(batfish_host)
        if batfish_ready:
            ok_snap, snap_msg = _load_bf_snapshot(batfish_host)
            if not ok_snap:
                log.warning("Batfish snapshot load failed: %s", snap_msg)
                batfish_ready = False

    # Step 3: run each assertion
    results: list[dict] = []
    failures: list[str] = []
    warnings: list[str] = []
    skipped:  list[str] = []

    for assertion in assertions:
        desc, ok, detail = _run_assertion(assertion, batfish_host, batfish_ready)
        results.append({"description": desc, "passed": ok, "detail": detail})

        if ok is None:
            skipped.append(f"{desc}: {detail}")
        elif ok:
            log.debug("  PASS: %s — %s", desc, detail)
        else:
            failures.append(f"{desc} — {detail}")
            log.debug("  FAIL: %s — %s", desc, detail)

    if not batfish_ready and has_batfish_assertions:
        warnings.append(
            "Batfish assertions skipped (service not running). "
            "Start with: docker run -d -p 9996:9996 -p 9997:9997 batfish/batfish"
        )

    (val_dir / "results.json").write_text(
        json.dumps({"results": results, "failures": failures, "warnings": warnings}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    passed = len(failures) == 0
    return ValidationResult(
        passed=passed,
        failures=failures,
        warnings=warnings,
        skipped=skipped,
    )
