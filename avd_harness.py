#!/usr/bin/env python3
"""
AVD 6.1.0 Evaluation Harness
18-662 Final Project — Mendoza & Silla

Runs 30 prompts × 4 models × 3 enforcement methods = 360 API calls.
Models within each prompt run in parallel; prompts run sequentially.

Usage:
    export OPENROUTER_API_KEY="sk-or-..."
    python3 avd_harness.py

Results written to: results/results_<timestamp>.json
                    results/results_<timestamp>.csv
"""

import os
import sys
import json
import csv
import time
import shutil
import subprocess
import asyncio
import aiohttp
import yaml
import logging
from datetime import datetime
from pathlib import Path
from copy import deepcopy

# ── Configuration ─────────────────────────────────────────────────────────────

AVD_EXAMPLE_DIR = Path.home() / "avd-eval"

GROUP_VARS = {
    "fabric":    AVD_EXAMPLE_DIR / "group_vars/FABRIC/fabric_variables.yml",
    "spines":    AVD_EXAMPLE_DIR / "group_vars/DC1_SPINES/spines.yml",
    "l3leaves":  AVD_EXAMPLE_DIR / "group_vars/DC1_L3_LEAVES/l3_leaves.yml",
    "netsvcs":   AVD_EXAMPLE_DIR / "group_vars/NETWORK_SERVICES/network_services.yml",
    "endpoints": AVD_EXAMPLE_DIR / "group_vars/CONNECTED_ENDPOINTS/connected_endpoints.yml",
}

MODELS = [
    "model-1-placeholder",   # TODO: replace with your OpenRouter model string
    "model-2-placeholder",   # TODO: replace with your OpenRouter model string
    "model-3-placeholder",   # TODO: replace with your OpenRouter model string
    "model-4-placeholder",   # TODO: replace with your OpenRouter model string
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RESULTS_DIR    = Path("results")
MAX_RETRIES    = 3
RETRY_DELAY    = 5   # seconds between retries
REQUEST_TIMEOUT= 120 # seconds

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(RESULTS_DIR / "harness.log" if RESULTS_DIR.exists() else "harness.log"),
    ]
)
log = logging.getLogger(__name__)

# ── Base file contents (loaded at startup) ────────────────────────────────────

def load_base_files():
    contents = {}
    for key, path in GROUP_VARS.items():
        with open(path) as f:
            contents[key] = f.read()
    return contents

# ── Prompt definitions ────────────────────────────────────────────────────────

def build_prompts(base_files):
    """Return list of dicts: {id, tier, file_key, task, why}"""
    return [
        # ── TIER 1: Shallow & Flat ─────────────────────────────────────────
        dict(id="P01", tier=1, file_key="fabric",
             task="Add a second NTP server '1.pool.ntp.org' to the existing ntp_settings.servers list.",
             why="Tests list append under ntp_settings.servers."),
        dict(id="P02", tier=1, file_key="fabric",
             task="Add a second DNS server with ip_address 8.8.8.8 to dns_settings.servers.",
             why="Tests exact key name dns_settings vs dns_servers vs name_servers."),
        dict(id="P03", tier=1, file_key="fabric",
             task="Change p2p_uplinks_mtu from 1500 to 9214.",
             why="Single integer scalar change."),
        dict(id="P04", tier=1, file_key="spines",
             task="Change the BGP ASN for all spines from 65100 to 65000.",
             why="Tests bgp_as vs autonomous_system vs router_bgp.as aliasing."),
        dict(id="P05", tier=1, file_key="spines",
             task="Add a third spine node named dc1-spine3 with id 3 and mgmt_ip 172.16.1.13/24.",
             why="List append of a node object under spine.nodes."),
        dict(id="P06", tier=1, file_key="l3leaves",
             task="Change the virtual_router_mac_address for all l3leaf nodes from 00:1c:73:00:00:99 to 00:1c:73:00:dc:01.",
             why="Tests exact key vs varp_mac / anycast_mac / virtual_mac_address aliases."),
        dict(id="P07", tier=1, file_key="l3leaves",
             task="Change spanning_tree_mode for all l3leaf nodes from mstp to rstp.",
             why="Tests spanning_tree_mode vs spanning_tree.mode (wrong nesting)."),
        dict(id="P08", tier=1, file_key="fabric",
             task="Add a password field with value 'arista123' to the evpn_overlay_peers BGP peer group.",
             why="Tests scalar string under bgp_peer_groups.evpn_overlay_peers.password."),
        dict(id="P09", tier=1, file_key="l3leaves",
             task="Set the spanning_tree_priority for all l3leaf nodes to 8192.",
             why="Tests spanning_tree_priority vs spanning_tree.priority (wrong nesting)."),
        dict(id="P10", tier=1, file_key="fabric",
             task="Add a new local user named 'netops' with privilege 15, role network-operator, and no_password set to true under aaa_settings.local_users.",
             why="List append of user object; tests no_password vs nopassword vs password_type."),

        # ── TIER 2: Deeply Nested Dicts ────────────────────────────────────
        dict(id="P11", tier=2, file_key="l3leaves",
             task="Add a filters.tags list containing 'DC1_L3_LEAF1' to the DC1_L3_LEAF1 node_group.",
             why="Key addition inside specific named node_group; tests correct depth."),
        dict(id="P12", tier=2, file_key="l3leaves",
             task="Add evpn_gateway.remote_peers as an empty list under dc1-leaf1a's node definition inside DC1_L3_LEAF1.",
             why="Node-level key inside node_group->nodes->node three-level path."),
        dict(id="P13", tier=2, file_key="netsvcs",
             task="Add a vtep_diagnostic block to VRF11 with loopback: 12 and loopback_ip_range: 10.255.12.0/27.",
             why="Dict insertion at tenant->VRF depth; VRF11 currently has no vtep_diagnostic."),
        dict(id="P14", tier=2, file_key="netsvcs",
             task="Add a static_routes list to VRF10 containing one route: destination_address_prefix 0.0.0.0/0, gateway 10.10.10.1.",
             why="Tests AVD 5.x+ renamed keys destination_address_prefix and gateway."),
        dict(id="P15", tier=2, file_key="netsvcs",
             task="Set ip_address_virtual to 10.10.11.254/24 for SVI id 11 inside VRF10, overriding the current value.",
             why="Value change at tenant->VRF->SVI depth; must target correct SVI in correct VRF."),
        dict(id="P16", tier=2, file_key="netsvcs",
             task="Add a new VRF named VRF20 with vrf_vni 20 to TENANT1. Include one SVI: id 31, name VRF20_VLAN31, enabled true, ip_address_virtual 10.10.31.1/24.",
             why="Full VRF insertion; tests vrf_vni vs vrf_id vs vni key names."),
        dict(id="P17", tier=2, file_key="l3leaves",
             task="Add uplink_switch_interfaces ['Ethernet1', 'Ethernet1'] to the l3leaf defaults section.",
             why="Tests uplink_switch_interfaces vs uplink_interfaces (those are the local interfaces)."),
        dict(id="P18", tier=2, file_key="netsvcs",
             task="Add a new l2vlan to TENANT1 with id 3403 and name L2_VLAN3403.",
             why="List append inside tenants[TENANT1].l2vlans; tests l2vlans vs vlans vs l2_vlans."),
        dict(id="P19", tier=2, file_key="l3leaves",
             task="Add mlag_port_channel_id: 2000 at the DC1_L3_LEAF2 node_group level (not defaults, not node level).",
             why="Tests three-scope distinction: defaults vs node_group vs node."),
        dict(id="P20", tier=2, file_key="netsvcs",
             task="Add tags: ['DC1_L3_LEAF1'] to SVI id 11 inside VRF10 so it deploys only on leaf pair 1.",
             why="Key addition at tenant->VRF->SVI depth; must target correct SVI by id."),

        # ── TIER 3: Arrays & Conditional Logic ─────────────────────────────
        dict(id="P21", tier=3, file_key="netsvcs",
             task="Add two new SVIs to VRF10: id 13 (name VRF10_VLAN13, ip_address_virtual 10.10.13.1/24, enabled true) and id 14 (name VRF10_VLAN14, ip_address_virtual 10.10.14.1/24, enabled true).",
             why="Two-item array append in specific VRF; tests ip_address_virtual vs ip_address."),
        dict(id="P22", tier=3, file_key="endpoints",
             task="Add a new server named dc1-leaf1-server2 with adapters connecting dc1-leaf1a Ethernet6 and dc1-leaf1b Ethernet6, endpoint_ports [PCI1, PCI2], vlans 11-12, mode trunk, spanning_tree_portfast edge, port_channel mode active.",
             why="Full server object with nested adapters array and port_channel nesting."),
        dict(id="P23", tier=3, file_key="netsvcs",
             task="Add a new tenant named TENANT2 with mac_vrf_vni_base 20000, VRF VRF20 (vrf_vni 20), and one SVI: id 100, name TENANT2_VLAN100, enabled true, ip_address_virtual 10.20.100.1/24.",
             why="Entire new tenant object appended to top-level tenants list."),
        dict(id="P24", tier=3, file_key="l3leaves",
             task="Add a third node_group DC1_L3_LEAF3 with bgp_as 65103 containing two nodes: dc1-leaf3a (id 5, mgmt_ip 172.16.1.105/24) and dc1-leaf3b (id 6, mgmt_ip 172.16.1.106/24).",
             why="Full node_group with two nodes; tests group vs name key, id sequencing."),
        dict(id="P25", tier=3, file_key="endpoints",
             task="Add an iLO adapter to dc1-leaf2-server1: endpoint_ports [iLO], switch_ports [Ethernet6], switches [dc1-leaf2c], vlans 21, mode access, spanning_tree_portfast edge.",
             why="Adapter append to existing server's adapters array without disturbing existing adapters."),
        dict(id="P26", tier=3, file_key="netsvcs",
             task="Add structured_config.description: 'VRF10_VLAN12_CUSTOM' to SVI id 12 in VRF10.",
             why="structured_config escape hatch key at correct SVI level; tests sub-key placement."),
        dict(id="P27", tier=3, file_key="fabric",
             task="Add NTP servers 1.pool.ntp.org and 2.pool.ntp.org to ntp_settings.servers, AND set prefer: true on the existing 0.pool.ntp.org server.",
             why="Multi-item append AND modify existing item in same list in one operation."),
        dict(id="P28", tier=3, file_key="l3leaves",
             task="For node dc1-leaf1a inside DC1_L3_LEAF1, add a structured_config block containing loopback_interfaces: [{name: Loopback0, description: 'ROUTER-ID'}].",
             why="structured_config at node level inside node_group->nodes->node; array inside structured_config."),
        dict(id="P29", tier=3, file_key="netsvcs",
             task="Add ip_helpers to SVI id 21 in VRF11 as a list with one entry: ip_address 10.255.0.1, source_interface Loopback0.",
             why="ip_helpers is list-of-objects not list-of-strings; common failure is using string or placing at VRF level."),
        dict(id="P30", tier=3, file_key="l3leaves",
             task="Add evpn_services_l2_only: false to l3leaf.defaults, AND add evpn_services_l2_only: true as a node-level override on dc1-leaf2a only inside DC1_L3_LEAF2.",
             why="Same key at two different AVD scoping levels (defaults and node override) in one operation."),
    ]

# ── System prompts per enforcement method ─────────────────────────────────────

SYSTEM_BASELINE = (
    "You are a network automation assistant working with Arista Validated Designs (AVD) version 6.1.0."
)

SYSTEM_PROMPT_ENFORCED = (
    "You are a network automation assistant working with Arista Validated Designs (AVD) version 6.1.0. "
    "You MUST respond with ONLY raw YAML. "
    "No markdown fences, no explanation, no code blocks, no extra text of any kind. "
    "Your entire response must be directly parseable by Python's yaml.safe_load(). "
    "Any non-YAML text will cause a fatal pipeline error."
)

def build_user_message(prompt, base_content, method):
    base = (
        f"TASK:\n{prompt['task']}\n\n"
        f"TARGET FILE: {prompt['file_key']}\n\n"
        f"CURRENT FILE CONTENTS:\n{base_content}\n\n"
    )
    if method == "baseline":
        return base + "Please provide the modified file contents to accomplish the task above."
    else:
        return (
            base +
            "INSTRUCTIONS:\n"
            "- Output ONLY the complete modified YAML file contents.\n"
            "- Do NOT include any markdown fences, explanation, or extra text.\n"
            "- Preserve all existing keys and values exactly unless the task requires changing them.\n"
            "- Your entire response must be parseable by Python yaml.safe_load().\n\n"
            "Remember: output ONLY the raw YAML. No ```yaml``` or any surrounding text."
        )

# ── API call ──────────────────────────────────────────────────────────────────

async def call_api(session, model, system_prompt, user_message, method, api_key):
    """Make one API call. Returns (raw_text, error_str)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cmu.edu/18662",
        "X-Title": "18-662-AVD-Eval",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }

    # API-level JSON mode only applies to OpenAI models on OpenRouter
    if method == "api_json_mode" and model.startswith("openai/"):
        payload["response_format"] = {"type": "json_object"}
        # Rewrite system prompt to request JSON for json_mode
        payload["messages"][0]["content"] = system_prompt.replace(
            "raw YAML", "raw JSON"
        )
        payload["messages"][1]["content"] = user_message.replace(
            "YAML", "JSON"
        ).replace("yaml.safe_load()", "json.loads()")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.post(
                OPENROUTER_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                if resp.status == 429:
                    wait = RETRY_DELAY * attempt
                    log.warning(f"Rate limited on {model}, waiting {wait}s (attempt {attempt})")
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    text = await resp.text()
                    return None, f"HTTP {resp.status}: {text[:200]}"
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                return raw, None
        except asyncio.TimeoutError:
            log.warning(f"Timeout on {model} attempt {attempt}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            return None, str(e)

    return None, f"All {MAX_RETRIES} retries failed"

# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate_response(raw_text, method):
    """
    Returns dict:
      yaml_valid     : bool
      no_extra_text  : bool  (no markdown fences or prose before/after)
      yaml_content   : str | None  (cleaned YAML if parseable)
      parse_error    : str | None
    """
    if raw_text is None:
        return dict(yaml_valid=False, no_extra_text=False, yaml_content=None, parse_error="No response")

    # Check for extra text
    stripped = raw_text.strip()
    has_fence = stripped.startswith("```") or "```yaml" in stripped or "```json" in stripped
    no_extra_text = not has_fence

    # Try to clean fences for YAML parse attempt
    cleaned = stripped
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        # Remove first line (```yaml or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        cleaned = "\n".join(lines)

    # For json_mode: try JSON parse first, convert to YAML string
    if method == "api_json_mode":
        try:
            import json as jsonlib
            obj = jsonlib.loads(cleaned)
            yaml_content = yaml.dump(obj, default_flow_style=False)
            return dict(yaml_valid=True, no_extra_text=no_extra_text, yaml_content=yaml_content, parse_error=None)
        except Exception:
            pass

    # Try YAML parse
    try:
        yaml.safe_load(cleaned)
        return dict(yaml_valid=True, no_extra_text=no_extra_text, yaml_content=cleaned, parse_error=None)
    except yaml.YAMLError as e:
        return dict(yaml_valid=False, no_extra_text=no_extra_text, yaml_content=None, parse_error=str(e)[:300])

def run_playbook(file_key, yaml_content):
    """
    Write yaml_content to the target group_vars file, run build.yml,
    restore the original. Returns (playbook_pass, playbook_output).
    """
    target_path = GROUP_VARS[file_key]
    backup_path = target_path.with_suffix(".yml.bak")

    # Backup original
    shutil.copy2(target_path, backup_path)

    try:
        # Write model output
        with open(target_path, "w") as f:
            f.write(yaml_content)

        # Run playbook
        result = subprocess.run(
            ["ansible-playbook", "build.yml"],
            cwd=AVD_EXAMPLE_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        passed = result.returncode == 0
        output = (result.stdout + result.stderr)[-2000:]  # last 2000 chars
        return passed, output

    except subprocess.TimeoutExpired:
        return False, "Playbook timed out after 300s"
    except Exception as e:
        return False, str(e)
    finally:
        # Always restore original
        shutil.copy2(backup_path, target_path)
        backup_path.unlink(missing_ok=True)

# ── Per-prompt runner (parallel across models) ────────────────────────────────

async def run_prompt(session, prompt, base_content, api_key, results):
    """Run one prompt across all models and methods. Appends to results list."""
    prompt_id = prompt["id"]
    log.info(f"▶  Starting {prompt_id} — {prompt['task'][:60]}…")

    methods = {
        "baseline":        (SYSTEM_BASELINE,        False),
        "prompt_enforced": (SYSTEM_PROMPT_ENFORCED,  False),
        "api_json_mode":   (SYSTEM_PROMPT_ENFORCED,  True),
    }

    # Build all (model, method) tasks for this prompt
    tasks = []
    combos = []
    for method, (sys_prompt, _) in methods.items():
        user_msg = build_user_message(prompt, base_content, method)
        for model in MODELS:
            tasks.append(call_api(session, model, sys_prompt, user_msg, method, api_key))
            combos.append((model, method))

    # Fire all in parallel
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    for (model, method), response in zip(combos, responses):
        if isinstance(response, Exception):
            raw_text, api_error = None, str(response)
        else:
            raw_text, api_error = response

        # Layer 1 & 3 evaluation
        eval_result = evaluate_response(raw_text, method)

        # Layer 2: playbook validation (only if YAML is valid)
        playbook_pass   = None
        playbook_output = None
        if eval_result["yaml_valid"] and eval_result["yaml_content"]:
            playbook_pass, playbook_output = run_playbook(
                prompt["file_key"], eval_result["yaml_content"]
            )
            log.info(
                f"   {prompt_id} | {model.split('/')[-1]:20s} | {method:16s} | "
                f"yaml={'✓' if eval_result['yaml_valid'] else '✗'} | "
                f"playbook={'✓' if playbook_pass else '✗'}"
            )
        else:
            log.info(
                f"   {prompt_id} | {model.split('/')[-1]:20s} | {method:16s} | "
                f"yaml=✗ | skipping playbook"
            )

        results.append({
            "prompt_id":        prompt_id,
            "tier":             prompt["tier"],
            "file_key":         prompt["file_key"],
            "task":             prompt["task"][:120],
            "model":            model,
            "method":           method,
            "api_error":        api_error,
            "yaml_valid":       eval_result["yaml_valid"],
            "no_extra_text":    eval_result["no_extra_text"],
            "yaml_parse_error": eval_result["parse_error"],
            "playbook_pass":    playbook_pass,
            "playbook_output":  playbook_output,
            "raw_response":     (raw_text or "")[:500],  # truncate for storage
            "timestamp":        datetime.utcnow().isoformat(),
        })

# ── Results writer ────────────────────────────────────────────────────────────

def write_results(results, timestamp):
    RESULTS_DIR.mkdir(exist_ok=True)

    # JSON (full detail)
    json_path = RESULTS_DIR / f"results_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"JSON results → {json_path}")

    # CSV (analysis-ready)
    csv_path = RESULTS_DIR / f"results_{timestamp}.csv"
    fields = [
        "prompt_id", "tier", "file_key", "model", "method",
        "yaml_valid", "no_extra_text", "playbook_pass",
        "api_error", "yaml_parse_error", "task", "timestamp"
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log.info(f"CSV  results → {csv_path}")

    # Summary printout
    print_summary(results)

def print_summary(results):
    from collections import defaultdict

    print("\n" + "="*70)
    print("RESULTS SUMMARY")
    print("="*70)

    total = len(results)
    yaml_ok = sum(1 for r in results if r["yaml_valid"])
    no_extra = sum(1 for r in results if r["no_extra_text"])
    pb_ran   = [r for r in results if r["playbook_pass"] is not None]
    pb_ok    = sum(1 for r in pb_ran if r["playbook_pass"])

    print(f"Total calls        : {total}")
    print(f"YAML valid         : {yaml_ok}/{total} ({100*yaml_ok//total}%)")
    print(f"No extra text      : {no_extra}/{total} ({100*no_extra//total}%)")
    print(f"Playbook pass      : {pb_ok}/{len(pb_ran)} ({100*pb_ok//len(pb_ran) if pb_ran else 0}%)")

    print("\n── By Method ──")
    by_method = defaultdict(list)
    for r in results:
        by_method[r["method"]].append(r)
    for method, rows in sorted(by_method.items()):
        pb = [r for r in rows if r["playbook_pass"] is not None]
        pb_p = sum(1 for r in pb if r["playbook_pass"])
        print(f"  {method:20s}  yaml={sum(1 for r in rows if r['yaml_valid'])}/{len(rows)}  "
              f"playbook={pb_p}/{len(pb)}")

    print("\n── By Model ──")
    by_model = defaultdict(list)
    for r in results:
        by_model[r["model"]].append(r)
    for model, rows in sorted(by_model.items()):
        pb = [r for r in rows if r["playbook_pass"] is not None]
        pb_p = sum(1 for r in pb if r["playbook_pass"])
        print(f"  {model.split('/')[-1]:25s}  yaml={sum(1 for r in rows if r['yaml_valid'])}/{len(rows)}  "
              f"playbook={pb_p}/{len(pb)}")

    print("\n── By Tier ──")
    by_tier = defaultdict(list)
    for r in results:
        by_tier[r["tier"]].append(r)
    for tier in sorted(by_tier):
        rows = by_tier[tier]
        pb = [r for r in rows if r["playbook_pass"] is not None]
        pb_p = sum(1 for r in pb if r["playbook_pass"])
        print(f"  Tier {tier}  yaml={sum(1 for r in rows if r['yaml_valid'])}/{len(rows)}  "
              f"playbook={pb_p}/{len(pb)}")

    print("="*70 + "\n")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENROUTER_API_KEY environment variable not set.")

    # Validate AVD directory exists
    if not AVD_EXAMPLE_DIR.exists():
        sys.exit(f"ERROR: AVD example dir not found: {AVD_EXAMPLE_DIR}")
    for key, path in GROUP_VARS.items():
        if not path.exists():
            sys.exit(f"ERROR: group_vars file not found: {path}")

    RESULTS_DIR.mkdir(exist_ok=True)

    # Load base file contents once
    log.info("Loading base group_vars files…")
    base_files = load_base_files()

    # Build prompt list
    prompts = build_prompts(base_files)
    log.info(f"Loaded {len(prompts)} prompts × {len(MODELS)} models × 3 methods = "
             f"{len(prompts) * len(MODELS) * 3} total calls")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    results = []

    # Run prompts sequentially, models in parallel within each prompt
    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i, prompt in enumerate(prompts, 1):
            log.info(f"\n[{i:02d}/{len(prompts)}] Prompt {prompt['id']} (Tier {prompt['tier']})")
            base_content = base_files[prompt["file_key"]]
            await run_prompt(session, prompt, base_content, api_key, results)

            # Write incremental results after each prompt (crash safety)
            write_results(results, timestamp)

    log.info("All prompts complete.")
    write_results(results, timestamp)

if __name__ == "__main__":
    asyncio.run(main())
