#!/usr/bin/env python3
"""
Semantic validation: for each generated config.yaml under results/, temporarily
install the merged file into the repo group_vars path from manifest.json, run
ansible-playbook build.yml, restore baseline, and record outcome on the manifest.

Ansible is invoked with an isolated environment so a global ANSIBLE_CONFIG (e.g.
pointing at ~/avd-eval) cannot hijack the run: we force ANSIBLE_CONFIG to this
repo's ansible.cfg when present, drop ANSIBLE_INVENTORY, use explicit -i, and
set cwd to the repo root.

``ansible-galaxy collection install arista.avd`` does **not** install AVD's **pip**
dependencies (``anta``, ``aristaproto``, ``pyavd``, …). Use the same venv as
Ansible: ``.venv/bin/pip install -r requirements.txt``. This script prepends
``<repo>/.venv/bin`` to PATH for the ``ansible-playbook`` child process when that
folder exists.

Usage:
  python3 validate_configs.py [--repo-root DIR] [--results-dir results] [--limit N] [-v] [--report-dir DIR]

  ``--report-dir`` is a folder under ``experiments/`` (relative to repo root unless absolute); each run
  writes a timestamped ``validate_configs_<UTC>.txt`` mirroring the terminal (line-flushed as it runs).
  Omit ``--report-dir`` to use ``experiments/auto_<UTC>/`` automatically.

Use --repo-root when your artifacts were generated against a specific clone; it
must be the same tree whose group_vars/ files you are patching (same paths as
manifest context_file).

Use -v / --verbose for DEBUG (ansible command, per-step file copies, full playbook tail).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from experiment_report import configure_run_logging

SCRIPT_DIR = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


def _relative_under(base: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def extract_ansible_error_lines(combined_output: str, *, max_lines: int = 200) -> list[str]:
    """Pull AVD/Ansible error lines for concise console logs (full output stays in manifest)."""
    out: list[str] = []
    for line in combined_output.splitlines():
        s = line.strip()
        if "[ERROR]:" in s or s.startswith("fatal:"):
            if s not in out:
                out.append(s)
            if len(out) >= max_lines:
                break
    return out


def log_error_lines_brief(err_lines: list[str], *, max_out: int = 10) -> None:
    """
    Log summary + fatal lines first, then one example per distinct validation message
    (AVD repeats the same validation for every host).
    """
    out: list[str] = []
    for line in err_lines:
        if "Validation error" in line:
            continue
        if line not in out:
            out.append(line)
        if len(out) >= 3:
            break

    seen_val: set[str] = set()
    for line in err_lines:
        if "Validation error" not in line:
            continue
        m = re.search(r"input data model '([^']+)': (.+)$", line)
        key = f"{m.group(1)}|{m.group(2)}" if m else line
        if key in seen_val:
            continue
        seen_val.add(key)
        out.append(line)
        if len(out) >= max_out:
            break

    for el in out:
        log.error("  %s", el)
    if len(err_lines) > len(out):
        log.error(
            "  ... +%s line(s) not shown (often duplicate hosts); full tail in manifest.json -> semantic_validation.ansible_tail",
            len(err_lines) - len(out),
        )


# Distribution names checked by arista.avd.verify_requirements (collection requirements.txt).
_AVD_PIP_DIST_NAMES = (
    "anta",
    "aristaproto",
    "cryptography",
    "deepmerge",
    "distlib",
    "grpclib",
    "jinja2",
    "netaddr",
    "pyavd",
    "pyavd-utils",
    "python-socks",
    "pyyaml",
    "requests",
)


def _interpreters_for_avd_check(repo_root: Path) -> list[str]:
    """Prefer ``<repo>/.venv/bin/python*``, then the interpreter running this script."""
    out: list[str] = []
    vbin = (repo_root.resolve() / ".venv" / "bin")
    if vbin.is_dir():
        for name in ("python3", "python"):
            p = vbin / name
            if p.is_file():
                s = str(p.resolve())
                if s not in out:
                    out.append(s)
    ex = sys.executable
    if ex and ex not in out:
        out.append(ex)
    return out


def verify_avd_pip_packages(repo_root: Path) -> tuple[bool, str]:
    """
    Fail fast if the AVD pip stack is missing (same check eos_designs runs first).
    Returns (True, interpreter_used) or (False, comma-separated interpreters tried).
    """
    candidates = _interpreters_for_avd_check(repo_root)
    if not candidates:
        return False, "(no python interpreter found)"

    snippet = (
        "import importlib.metadata as m\n"
        "names = %r\n"
        "for n in names:\n"
        "    try:\n"
        "        m.version(n)\n"
        "    except m.PackageNotFoundError:\n"
        "        raise SystemExit('missing:' + n)\n"
    ) % (_AVD_PIP_DIST_NAMES,)

    for py in candidates:
        proc = subprocess.run([py, "-c", snippet], capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return True, py
    return False, ", ".join(candidates)


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

    vbin = (repo_root / ".venv" / "bin").resolve()
    if vbin.is_dir():
        path_key = "PATH"
        prefix = str(vbin) + os.pathsep
        prev = env.get(path_key, "")
        if not prev.startswith(prefix):
            env[path_key] = prefix + prev
            log.debug("Prepended %s to %s for ansible-playbook subprocess", vbin, path_key)

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
    log.debug(
        "ansible-playbook %s | cwd=%s | ANSIBLE_CONFIG=%s",
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
        log.debug(
            "ansible-playbook rc=%s elapsed=%.2fs chars=%s",
            proc.returncode,
            elapsed,
            len(text),
        )
        if not ok:
            err_lines = extract_ansible_error_lines(text)
            if err_lines:
                log.debug("ansible error lines (for -v):\n%s", "\n".join(err_lines[:25]))
            else:
                log.debug("ansible tail (no [ERROR]: lines; first 1500 chars):\n%s", text[:1500])
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


def validate_one(
    artifact_dir: Path,
    repo_root: Path,
    results_dir: Path,
    index: int,
    total: int,
) -> None:
    manifest_path = artifact_dir / "manifest.json"
    config_path = artifact_dir / "config.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = manifest.get("context_file")
    tid = manifest.get("task_id", "?")
    model = manifest.get("model", "?")
    slug = manifest.get("task_slug", "?")
    art_rel = _relative_under(results_dir, artifact_dir)

    if not rel:
        log.warning("[%s/%s] %s %s | SKIP | no context_file in manifest", index, total, tid, model)
        return

    repo_root = repo_root.resolve()
    target = repo_root / rel
    if not target.is_file():
        log.warning(
            "[%s/%s] %s %s | SKIP | missing target %s",
            index,
            total,
            tid,
            model,
            rel,
        )
        manifest["semantic_validation"] = {
            "ran_at_utc": datetime.utcnow().isoformat() + "Z",
            "ok": False,
            "error": f"target file not found: {rel}",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    backup = target.with_suffix(target.suffix + ".validate_bak")
    ok = False
    log.debug(
        "flow: backup %s <- baseline %s ; patch <- %s ; ansible ; restore",
        backup.name,
        rel,
        _relative_under(artifact_dir, config_path),
    )

    try:
        shutil.copy2(target, backup)
        shutil.copy2(config_path, target)
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
    finally:
        if backup.is_file():
            shutil.copy2(backup, target)
            backup.unlink(missing_ok=True)
        else:
            log.warning("[%s/%s] %s %s | no backup at %s — restore skipped", index, total, tid, model, backup)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # One-line summary + actionable errors only when failed
    order = "backup(baseline) -> write artifact -> ansible-playbook build.yml -> restore baseline"
    if ok:
        log.info(
            "[%s/%s] %s %s | OK | %s | context=%s | %s",
            index,
            total,
            tid,
            model,
            slug,
            rel,
            order,
        )
    else:
        log.error(
            "[%s/%s] %s %s | FAIL | %s | context=%s | artifact=%s | %s",
            index,
            total,
            tid,
            model,
            slug,
            rel,
            art_rel,
            order,
        )
        err_lines = extract_ansible_error_lines(output)
        if err_lines:
            log_error_lines_brief(err_lines)
        else:
            log.error("  (no [ERROR]: lines in tail; see manifest semantic_validation.ansible_tail)")


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
    p.add_argument(
        "--skip-anta-check",
        action="store_true",
        help="Do not exit early if AVD pip packages are missing (not recommended).",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Experiment folder for this run's log (relative to experiments/ unless absolute). "
        "Default: experiments/auto_<UTC>/. A line-flushed validate_configs_<UTC>.txt mirrors stderr.",
    )
    args = p.parse_args()

    repo_root = (args.repo_root or SCRIPT_DIR).resolve()
    report_path = configure_run_logging(
        repo_root=repo_root,
        report_dir=args.report_dir,
        console_level=logging.DEBUG if args.verbose else logging.INFO,
        log_format="%(asctime)s  %(levelname)-7s  %(message)s",
        report_basename="validate_configs",
    )
    log.info("Run report (continuous): %s", report_path)

    results_dir = (args.results_dir or (repo_root / "results")).resolve()

    log.info(
        "validate_configs | repo_root=%s | results_dir=%s",
        repo_root,
        results_dir,
    )

    if not args.skip_anta_check:
        ok, detail = verify_avd_pip_packages(repo_root)
        if not ok:
            log.error(
                "AVD pip deps missing (same env as ansible-playbook). Tried: %s | "
                "Fix: %s/.venv/bin/pip install -r requirements.txt | --skip-anta-check",
                detail,
                repo_root,
            )
            sys.exit(2)
        log.debug("AVD pip OK | interpreter=%s", detail)

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
    log.debug(
        "ansible child env: ANSIBLE_CONFIG=%s",
        str(cfg) if cfg.is_file() else "(unset)",
    )
    log.info("Preflight OK | %s artifact(s)", len(artifacts))

    total = len(artifacts)
    for i, d in enumerate(artifacts, start=1):
        validate_one(d, repo_root, results_dir, i, total)

    ok_n = sum(
        1
        for d in artifacts
        if json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        .get("semantic_validation", {})
        .get("ok")
    )
    log.info("Done | %s/%s passed semantic validation", ok_n, total)


if __name__ == "__main__":
    main()
