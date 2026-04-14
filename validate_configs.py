#!/usr/bin/env python3
"""
Semantic validation: for each generated config.yaml under results/, temporarily
install the merged file into the repo group_vars path from manifest.json, run
ansible-playbook build.yml, restore baseline, and record outcome on the manifest.

Ansible is invoked with an isolated environment so a global ANSIBLE_CONFIG (e.g.
pointing at ~/avd-eval) cannot hijack the run: we force ANSIBLE_CONFIG to this
repo's ansible.cfg when present, drop ANSIBLE_INVENTORY, use explicit -i, and
set cwd to the repo root.

Usage:
  python3 validate_configs.py [--repo-root DIR] [--results-dir results] [--limit N] [-v]

Use --repo-root when your artifacts were generated against a specific clone; it
must be the same tree whose group_vars/ files you are patching (same paths as
manifest context_file).

Use -v / --verbose for extra DEBUG lines (ansible output head on failure).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
log = logging.getLogger(__name__)

# Inherited from the shell, these commonly point at another clone (e.g. ~/avd-eval)
# and cause Ansible to validate the wrong tree while we patch repo group_vars.
_ENV_DROP_FOR_ANSIBLE_ISOLATION = (
    "ANSIBLE_INVENTORY",
    "ANSIBLE_INVENTORY_FILE",
)


def ansible_subprocess_env(repo_root: Path) -> dict:
    """Environment for ansible-playbook: prefer this repo's ansible.cfg, no stray inventory."""
    env = os.environ.copy()
    dropped = [k for k in _ENV_DROP_FOR_ANSIBLE_ISOLATION if k in env]
    for key in _ENV_DROP_FOR_ANSIBLE_ISOLATION:
        env.pop(key, None)

    cfg = (repo_root / "ansible.cfg").resolve()
    if cfg.is_file():
        env["ANSIBLE_CONFIG"] = str(cfg)
    else:
        env.pop("ANSIBLE_CONFIG", None)

    if dropped:
        log.debug("Dropped inherited env vars (re-set by isolation): %s", dropped)
    return env


def find_artifacts(results_dir: Path) -> list[Path]:
    """Directories that contain manifest.json and config.yaml."""
    out: list[Path] = []
    if not results_dir.is_dir():
        return out
    for manifest in sorted(results_dir.rglob("manifest.json")):
        d = manifest.parent
        if (d / "config.yaml").is_file():
            out.append(d)
    return out


def run_build(repo_root: Path) -> tuple[bool, str]:
    repo_root = repo_root.resolve()
    build_yml = repo_root / "build.yml"
    inventory = repo_root / "inventory.yml"
    env = ansible_subprocess_env(repo_root)

    cmd = [
        "ansible-playbook",
        str(build_yml),
        "-i",
        str(inventory),
    ]
    log.info(
        "Calling: %s | cwd=%s | ANSIBLE_CONFIG=%s",
        " ".join(cmd),
        repo_root,
        env.get("ANSIBLE_CONFIG", "(unset in child env)"),
    )

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        elapsed = time.perf_counter() - t0
        text = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        log.info(
            "ansible-playbook finished rc=%s elapsed=%.2fs captured_output_chars=%s",
            proc.returncode,
            elapsed,
            len(text),
        )
        if not ok:
            log.warning(
                "ansible-playbook failed (first 1200 chars of combined output):\n%s",
                text[:1200],
            )
        elif log.isEnabledFor(logging.DEBUG):
            log.debug("ansible-playbook combined output (first 2000 chars):\n%s", text[:2000])
        return ok, text[-8000:]
    except subprocess.TimeoutExpired:
        log.error("ansible-playbook exceeded 300s timeout")
        return False, "ansible-playbook timed out after 300s"
    except FileNotFoundError:
        log.error("'ansible-playbook' not found on PATH — install ansible-core")
        return False, "ansible-playbook executable not found"
    except Exception as e:
        log.exception("ansible-playbook raised: %s", e)
        return False, str(e)


def validate_one(artifact_dir: Path, repo_root: Path, index: int, total: int) -> None:
    manifest_path = artifact_dir / "manifest.json"
    config_path = artifact_dir / "config.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = manifest.get("context_file")
    tid = manifest.get("task_id", "?")
    model = manifest.get("model", "?")

    log.info(
        "[%s/%s] --- %s %s ---",
        index,
        total,
        tid,
        model,
    )
    log.info("  artifact_dir: %s", artifact_dir.resolve())

    if not rel:
        log.warning("  skip: no context_file in manifest")
        return

    repo_root = repo_root.resolve()
    target = repo_root / rel
    if not target.is_file():
        log.warning("  skip: target group_vars file missing: %s", target)
        manifest["semantic_validation"] = {
            "ran_at_utc": datetime.utcnow().isoformat() + "Z",
            "ok": False,
            "error": f"target file not found: {rel}",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    backup = target.with_suffix(target.suffix + ".validate_bak")
    ok = False
    log.info("  baseline (will restore): %s", target)
    log.info("  merged artifact source:   %s", config_path.resolve())
    log.info("  backup path:              %s", backup.resolve())

    try:
        log.info("  step: copy baseline -> backup")
        shutil.copy2(target, backup)
        log.info("  step: copy artifact config.yaml -> baseline path")
        shutil.copy2(config_path, target)
        log.info("  step: run ansible-playbook build.yml")
        ok, output = run_build(repo_root)
        manifest["semantic_validation"] = {
            "ran_at_utc": datetime.utcnow().isoformat() + "Z",
            "ok": ok,
            "ansible_tail": output,
            "repo_root_used": str(repo_root),
            "ansible_config": str(repo_root / "ansible.cfg")
            if (repo_root / "ansible.cfg").is_file()
            else None,
        }
        log.info("  step: semantic result ok=%s (tail %s chars stored in manifest)", ok, len(output))
    finally:
        if backup.is_file():
            log.info("  step: restore baseline from backup")
            shutil.copy2(backup, target)
            backup.unlink(missing_ok=True)
            log.info("  restored OK, removed backup")
        else:
            log.warning("  no backup file at %s — restore skipped", backup)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("  wrote manifest.json | semantic_ok=%s\n", ok)


def main() -> None:
    p = argparse.ArgumentParser(description="Run AVD build.yml against generated config.yaml artifacts.")
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Ansible project root (inventory + build.yml + group_vars). Default: directory of this script.",
    )
    p.add_argument("--results-dir", type=Path, default=None, help="Path to results/ (default: <repo-root>/results).")
    p.add_argument("--limit", type=int, default=0, help="Max artifacts to process (0 = all).")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging (e.g. ansible stdout head on success).",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    repo_root = (args.repo_root or SCRIPT_DIR).resolve()
    results_dir = (args.results_dir or (repo_root / "results")).resolve()

    log.info("validate_configs starting")
    log.info("  repo_root=%s", repo_root)
    log.info("  results_dir=%s", results_dir)
    log.info("  script_dir=%s", SCRIPT_DIR)

    artifacts = find_artifacts(results_dir)
    if args.limit:
        artifacts = artifacts[: args.limit]

    if not artifacts:
        log.info("No artifacts with manifest.json + config.yaml under %s — exiting.", results_dir)
        sys.exit(0)

    build_yml = repo_root / "build.yml"
    if not build_yml.is_file():
        log.error("build.yml not found: %s", build_yml)
        sys.exit(f"build.yml not found at {build_yml}")

    inv = repo_root / "inventory.yml"
    if not inv.is_file():
        log.error("inventory.yml not found: %s", inv)
        sys.exit(f"inventory.yml not found at {inv}")

    cfg = repo_root / "ansible.cfg"
    log.info("Preflight OK: build.yml and inventory.yml exist under repo_root")
    log.info(
        "Ansible isolation: child ANSIBLE_CONFIG will be %s",
        str(cfg) if cfg.is_file() else "unset (no local ansible.cfg)",
    )
    log.info("Discovered %s artifact(s) to validate", len(artifacts))

    total = len(artifacts)
    for i, d in enumerate(artifacts, start=1):
        validate_one(d, repo_root, i, total)

    ok_n = sum(
        1
        for d in artifacts
        if json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        .get("semantic_validation", {})
        .get("ok")
    )
    log.info("=" * 60)
    log.info("Done. Processed %s artifact(s) | semantic_ok=%s | semantic_fail=%s", total, ok_n, total - ok_n)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
