#!/usr/bin/env python3
"""
Semantic validation: for each generated config.yaml under results/, temporarily
install the merged file into the repo group_vars path from manifest.json, run
ansible-playbook build.yml, restore baseline, and record outcome on the manifest.

Usage:
  python3 validate_configs.py [--results-dir results] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


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
    try:
        proc = subprocess.run(
            ["ansible-playbook", "build.yml"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=300,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, text[-4000:]
    except subprocess.TimeoutExpired:
        return False, "ansible-playbook timed out after 300s"
    except Exception as e:
        return False, str(e)


def validate_one(artifact_dir: Path, repo_root: Path) -> None:
    manifest_path = artifact_dir / "manifest.json"
    config_path = artifact_dir / "config.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = manifest.get("context_file")
    if not rel:
        log.warning("Skipping %s — no context_file in manifest", artifact_dir)
        return

    target = repo_root / rel
    if not target.is_file():
        log.warning("Skipping %s — target missing: %s", artifact_dir, target)
        manifest["semantic_validation"] = {
            "ran_at_utc": datetime.utcnow().isoformat() + "Z",
            "ok": False,
            "error": f"target file not found: {rel}",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    backup = target.with_suffix(target.suffix + ".validate_bak")
    try:
        shutil.copy2(target, backup)
        shutil.copy2(config_path, target)
        ok, output = run_build(repo_root)
        manifest["semantic_validation"] = {
            "ran_at_utc": datetime.utcnow().isoformat() + "Z",
            "ok": ok,
            "ansible_tail": output,
        }
    finally:
        if backup.is_file():
            shutil.copy2(backup, target)
            backup.unlink(missing_ok=True)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(
        "%s %s semantic=%s",
        manifest.get("task_id"),
        manifest.get("model"),
        manifest["semantic_validation"].get("ok"),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    p = argparse.ArgumentParser(description="Run AVD build.yml against generated config.yaml artifacts.")
    p.add_argument("--results-dir", type=Path, default=REPO_ROOT / "results", help="Path to results/")
    p.add_argument("--limit", type=int, default=0, help="Max artifacts to process (0 = all).")
    args = p.parse_args()

    artifacts = find_artifacts(args.results_dir.resolve())
    if args.limit:
        artifacts = artifacts[: args.limit]

    if not artifacts:
        log.info("No artifacts with manifest.json + config.yaml under %s", args.results_dir)
        sys.exit(0)

    build_yml = REPO_ROOT / "build.yml"
    if not build_yml.is_file():
        sys.exit(f"build.yml not found at {build_yml}")

    for d in artifacts:
        validate_one(d, REPO_ROOT)

    ok_n = sum(
        1
        for d in artifacts
        if json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        .get("semantic_validation", {})
        .get("ok")
    )
    log.info("Processed %s artifacts, semantic ok: %s", len(artifacts), ok_n)


if __name__ == "__main__":
    main()
