#!/usr/bin/env python3
"""
AVD Agent — Web UI with CVP-style Change Control

Workflow
────────
1. Engineer submits a change request in the browser.
2. A git branch  avd/change/<run_id>  is created from main.
3. The AVD agent runs on that branch (YAML edit → Ansible build → Batfish validation).
4. On success, all changes are committed to the branch.
5. A per-device diff (group_vars + intended/configs) is shown in the browser.
6. Engineer clicks Approve → branch merged to main.
   Engineer clicks Reject  → branch deleted, working tree restored to main.

Only one run can execute at a time (git requires an exclusive working tree).

Usage
─────
  source .venv/bin/activate
  python3 web_app.py              # http://localhost:8000
  python3 web_app.py --port 9000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

import avd_agent as _agent

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = _agent.REPO_ROOT
RUNS_DIR  = _agent.RUNS_DIR
BASE_BRANCH = "main"

app = FastAPI(title="AVD Agent", docs_url=None, redoc_url=None)

# Serialises agent runs — only one at a time (shared working tree)
_run_lock: asyncio.Lock = asyncio.Lock()

# run_id → runtime state dict
_run_states: dict[str, dict] = {}

# run_id → SSE queue
_queues: dict[str, asyncio.Queue] = {}


# ── Pydantic models ───────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    request:         str
    model:           str  = _agent.DEFAULT_MODEL
    dry_run:         bool = False
    skip_validation: bool = False
    batfish_host:    str  = "localhost"


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(*args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["git"] + list(args),
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _current_branch() -> str:
    _, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out


def _create_branch(run_id: str) -> tuple[bool, str]:
    branch = f"avd/change/{run_id}"
    rc, _, err = _git("checkout", "-b", branch, BASE_BRANCH)
    if rc != 0:
        # Fallback: branch from HEAD if BASE_BRANCH doesn't exist yet
        rc2, _, err2 = _git("checkout", "-b", branch)
        if rc2 != 0:
            return False, err2
    return True, branch


def _commit_changes(task_text: str) -> tuple[bool, str]:
    _git("add", "group_vars/", "intended/")
    rc, out, err = _git(
        "commit", "-m", f"avd: {task_text[:72]}",
        "--author", "AVD Agent <avd-agent@local>",
    )
    if rc != 0:
        if "nothing to commit" in (out + err).lower():
            return True, "nothing-to-commit"
        return False, err
    return True, out


def _get_diff(branch: str) -> str:
    """Unified diff between BASE_BRANCH and branch — device configs only."""
    _, out, _ = _git(
        "diff", f"{BASE_BRANCH}...{branch}",
        "--", "intended/configs/",
    )
    return out


def _diff_from_git_log(run_id: str) -> str:
    """
    For runs that predate the patch-file mechanism, find the agent commit
    in git log by the AVD Agent author and return its diff against its parent.
    run_id is a timestamp like 20260418T020217Z — we find commits within
    ±5 min of that timestamp.
    """
    try:
        # Parse run_id timestamp
        dt = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        after  = (dt.replace(minute=max(0, dt.minute - 5))).strftime("%Y-%m-%dT%H:%M:%SZ")
        before = (dt.replace(minute=min(59, dt.minute + 5))).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""

    # Find AVD Agent commits in the time window
    _, log_out, _ = _git(
        "log", "--author=AVD Agent", "--format=%H",
        f"--after={after}", f"--before={before}",
        "--", "intended/configs/",
    )
    sha = log_out.strip().split("\n")[0].strip() if log_out.strip() else ""

    # Also check merge commits (avd/change/... merge)
    if not sha:
        _, log_out2, _ = _git(
            "log", "--merges", "--format=%H %s",
            f"--after={after}", f"--before={before}",
        )
        for line in log_out2.strip().splitlines():
            if "avd/change/" in line:
                sha = line.split()[0]
                break

    if not sha:
        return ""

    _, diff_out, _ = _git(
        "show", sha,
        "--", "intended/configs/",
    )
    return diff_out


def _merge_to_main(branch: str) -> tuple[bool, str]:
    rc, _, err = _git("checkout", BASE_BRANCH)
    if rc != 0:
        return False, f"checkout {BASE_BRANCH} failed: {err}"
    rc2, out2, err2 = _git(
        "merge", "--no-ff", branch,
        "-m", f"Merge {branch} into {BASE_BRANCH}",
    )
    if rc2 != 0:
        _git("merge", "--abort")
        _git("checkout", BASE_BRANCH)
        return False, f"merge failed: {err2}"
    _git("branch", "-d", branch)
    # Ensure working tree matches the merged main exactly
    _git("checkout", "--", "group_vars/", "intended/")
    return True, out2


def _discard_branch(branch: str) -> None:
    """Checkout BASE_BRANCH, delete the feature branch, and clean any
    untracked files that were only added in the discarded branch."""
    _git("checkout", BASE_BRANCH)
    _git("branch", "-D", branch)
    # Remove untracked files left behind (e.g. new device configs added by Ansible)
    _git("clean", "-fd", "--", "group_vars/", "intended/")


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(event_type: str, data: object) -> str:
    return f"data: {json.dumps({'type': event_type, 'msg': data})}\n\n"


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/run")
async def start_run(body: RunRequest) -> dict:
    api_key = _agent._load_api_key()
    if not api_key:
        raise HTTPException(400, "OPENROUTER_API_KEY not set in .env")

    if _run_lock.locked():
        raise HTTPException(409, "Another run is in progress. Please wait.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    queue: asyncio.Queue = asyncio.Queue()
    _queues[run_id] = queue
    _run_states[run_id] = {"status": "running", "branch": None, "task_text": body.request, "diff": ""}

    import argparse as _ap
    args = _ap.Namespace(
        model=body.model, dry_run=body.dry_run, yes=True,
        intent_only=False, verbose=False,
        skip_validation=body.skip_validation,
        batfish_host=body.batfish_host,
        run_id=run_id,
    )

    async def run() -> None:
        async with _run_lock:
            state = _run_states[run_id]

            # ── 1. Create feature branch ──────────────────────────────────
            ok, branch = _create_branch(run_id)
            if not ok:
                queue.put_nowait(_sse("fail", f"Could not create branch: {branch}"))
                queue.put_nowait(_sse("done", {"success": False}))
                state["status"] = "failed"
                return

            state["branch"] = branch
            queue.put_nowait(_sse("branch", branch))
            queue.put_nowait(_sse("info", f"Branch created: {branch}"))

            # ── 2. Run the agent ──────────────────────────────────────────
            def emit(etype: str, msg: str) -> None:
                queue.put_nowait(_sse(etype, msg))

            token = _agent._web_emit.set(emit)
            try:
                success = await _agent._run_agent(body.request, args)
            except Exception as exc:
                success = False
                queue.put_nowait(_sse("fail", f"Agent error: {exc}"))
            finally:
                _agent._web_emit.reset(token)

            # ── 3. On success (full or build-only): commit + capture diff ─
            build_ok = success is True or success == "validation_failed"
            val_warn = success == "validation_failed"

            if build_ok:
                ok_commit, _ = _commit_changes(body.request)
                if ok_commit:
                    diff = _get_diff(branch)
                    # Persist diff to disk so it survives server restarts
                    diff_path = _agent.RUNS_DIR / run_id / "config_diff.patch"
                    try:
                        diff_path.write_text(diff, encoding="utf-8")
                    except Exception:
                        pass
                    state.update({
                        "status": "pending_approval",
                        "diff": diff,
                        "validation_warning": val_warn,
                    })
                    queue.put_nowait(_sse("pending_approval", {
                        "run_id": run_id,
                        "branch": branch,
                        "has_diff": bool(diff.strip()),
                        "validation_warning": val_warn,
                    }))
                else:
                    state["status"] = "failed"
                    queue.put_nowait(_sse("warn", "Changes generated but could not be committed."))
            else:
                # Build itself failed — discard branch
                _discard_branch(branch)
                state["status"] = "failed"

            queue.put_nowait(_sse("done", {"success": bool(build_ok)}))

    asyncio.create_task(run())
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str) -> StreamingResponse:
    queue = _queues.get(run_id)
    if queue is None:
        raise HTTPException(404, "run_id not found")

    async def generate():
        yield ": keepalive\n\n"
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=60)
                yield chunk
                if '"type": "done"' in chunk:
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        _queues.pop(run_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/diff")
async def get_diff(run_id: str) -> dict:
    state = _run_states.get(run_id, {})
    diff = state.get("diff", "")

    # Fall back to the persisted patch file (survives server restarts)
    if not diff:
        diff_path = _agent.RUNS_DIR / run_id / "config_diff.patch"
        if diff_path.is_file():
            try:
                diff = diff_path.read_text(encoding="utf-8")
            except Exception:
                pass

    # Last resort: reconstruct from git log for pre-patch-file runs
    if not diff:
        diff = _diff_from_git_log(run_id)

    return {
        "diff":               diff,
        "branch":             state.get("branch", ""),
        "status":             state.get("status", ""),
        "validation_warning": state.get("validation_warning", False),
    }


@app.post("/api/runs/{run_id}/approve")
async def approve_run(run_id: str) -> dict:
    state = _run_states.get(run_id)
    if not state or state["status"] != "pending_approval":
        raise HTTPException(409, "No pending approval for this run.")
    ok, msg = _merge_to_main(state["branch"])
    if not ok:
        raise HTTPException(500, f"Merge failed: {msg}")
    state["status"] = "approved"
    return {"status": "approved", "branch": state["branch"]}


@app.post("/api/runs/{run_id}/reject")
async def reject_run(run_id: str) -> dict:
    state = _run_states.get(run_id)
    if not state or state["status"] not in ("pending_approval",):
        raise HTTPException(409, "Nothing to reject for this run.")
    _discard_branch(state["branch"])
    state["status"] = "rejected"
    return {"status": "rejected"}


@app.get("/api/runs")
async def list_runs() -> list:
    if not RUNS_DIR.is_dir():
        return []
    runs = []
    for d in sorted(RUNS_DIR.iterdir(), key=lambda p: p.name, reverse=True)[:20]:
        manifest = d / "intent.json"
        if not manifest.is_file():
            continue
        try:
            intent = json.loads(manifest.read_text())
            success = None
            for attempt in sorted(d.glob("attempt_*/ansible_output.txt")):
                txt = attempt.read_text(errors="ignore")
                if "failed=0" in txt and "unreachable=0" in txt:
                    success = True
                elif "failed=" in txt:
                    success = False
            live = _run_states.get(d.name, {})
            has_diff = (
                (d / "config_diff.patch").is_file()
                or bool(live.get("diff"))
                or live.get("status") in ("pending_approval", "approved")
            )
            runs.append({
                "run_id":  d.name,
                "ts":      d.name,
                "request": intent.get("task_text", ""),
                "success": success,
                "status":  live.get("status"),
                "branch":  live.get("branch"),
                "has_diff": has_diff,
                "validation_warning": live.get("validation_warning", False),
            })
        except Exception:
            continue
    return runs


@app.get("/api/models")
async def list_models() -> list:
    return [
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.4",
        "google/gemini-3.1-pro-preview",
    ]


# ── Embedded HTML ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AVD Agent</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f1117;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{background:#1a1d27;border-bottom:1px solid #2d3148;padding:13px 22px;display:flex;align-items:center;gap:12px;flex-shrink:0}
header h1{font-size:1.05rem;font-weight:600;color:#a5b4fc}
header span{font-size:.78rem;color:#475569}
.main{display:flex;flex:1;overflow:hidden}

/* ── left panel ── */
.left{width:340px;min-width:280px;background:#1a1d27;border-right:1px solid #2d3148;display:flex;flex-direction:column;padding:18px;gap:14px;overflow-y:auto;flex-shrink:0}
label{font-size:.78rem;color:#94a3b8;display:block;margin-bottom:4px}
textarea{width:100%;min-height:100px;background:#0f1117;border:1px solid #2d3148;border-radius:6px;color:#e2e8f0;font-size:.88rem;padding:9px 11px;resize:vertical;outline:none;transition:border-color .15s}
textarea:focus{border-color:#6366f1}
select,input[type=text]{width:100%;background:#0f1117;border:1px solid #2d3148;border-radius:6px;color:#e2e8f0;font-size:.83rem;padding:7px 9px;outline:none}
select:focus,input[type=text]:focus{border-color:#6366f1}
.row{display:flex;align-items:center;gap:8px}
.row label{margin:0;cursor:pointer}
input[type=checkbox]{accent-color:#6366f1;width:13px;height:13px;cursor:pointer}
.btn{width:100%;padding:9px;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;transition:background .15s,opacity .15s}
.btn-run{background:#6366f1;color:#fff}
.btn-run:hover{background:#4f46e5}
.btn-run:disabled{opacity:.45;cursor:not-allowed}
.lock-msg{font-size:.75rem;color:#f59e0b;text-align:center;display:none}
.hist h3{font-size:.75rem;color:#475569;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.hist-item{padding:7px 9px;border-radius:5px;background:#0f1117;border:1px solid #2d3148;margin-bottom:5px;font-size:.75rem;cursor:pointer;transition:border-color .15s}
.hist-item:hover{border-color:#6366f1}
.hist-req{color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hist-meta{color:#475569;margin-top:2px}
.badge{display:inline-block;border-radius:3px;padding:1px 5px;font-size:.68rem;font-weight:700;margin-right:3px}
.ok{background:#14532d;color:#86efac}.fail{background:#450a0a;color:#fca5a5}.pending{background:#1e1b4b;color:#a5b4fc}

/* ── right panel ── */
.right{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* log */
.log-wrap{flex:1;display:flex;flex-direction:column;overflow:hidden;transition:flex .3s}
.log-hdr{padding:10px 18px;border-bottom:1px solid #2d3148;display:flex;align-items:center;gap:10px;font-size:.82rem;color:#64748b;background:#1a1d27;flex-shrink:0}
.branch-tag{font-family:monospace;font-size:.75rem;background:#1e1b4b;color:#818cf8;border:1px solid #3730a3;border-radius:4px;padding:2px 7px}
#log{flex:1;overflow-y:auto;padding:14px 18px;font-family:"JetBrains Mono","Fira Code",Menlo,monospace;font-size:.8rem;line-height:1.75;background:#0f1117}
.l-banner{margin:10px 0 4px;color:#818cf8;font-weight:700;border-bottom:1px solid #2d3148;padding-bottom:3px}
.l-step{color:#38bdf8}.l-ok{color:#4ade80}.l-fail{color:#f87171}.l-warn{color:#fbbf24}.l-info{color:#475569}
.l-done-ok{color:#4ade80;font-weight:700;margin-top:8px}.l-done-fail{color:#f87171;font-weight:700;margin-top:8px}

/* placeholder */
.ph{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:8px;color:#334155}
.ph p{font-size:.82rem}

/* ── diff / approval panel ── */
.approval{flex-shrink:0;border-top:2px solid #6366f1;background:#0f1117;display:none;flex-direction:column;max-height:55vh}
.approval.open{display:flex}
.apr-hdr{padding:10px 18px;background:#1a1d27;display:flex;align-items:center;gap:10px;flex-shrink:0}
.apr-hdr span{font-size:.88rem;font-weight:600;color:#a5b4fc;flex:1}
.apr-hdr .sub{font-size:.75rem;color:#64748b}
.btn-approve{background:#15803d;color:#fff;padding:7px 20px;font-size:.85rem;font-weight:600;border:none;border-radius:5px;cursor:pointer;transition:background .15s}
.btn-approve:hover{background:#166534}
.btn-reject{background:#991b1b;color:#fff;padding:7px 18px;font-size:.85rem;font-weight:600;border:none;border-radius:5px;cursor:pointer;margin-left:6px;transition:background .15s}
.btn-reject:hover{background:#7f1d1d}
.btn-approve:disabled,.btn-reject:disabled{opacity:.45;cursor:not-allowed}

/* diff viewer */
.diff-container{overflow-y:auto;flex:1;padding:0}
.diff-file{border-bottom:1px solid #1e293b}
.diff-file-hdr{padding:6px 16px;background:#161b27;font-family:monospace;font-size:.75rem;color:#94a3b8;cursor:pointer;display:flex;align-items:center;gap:8px;user-select:none}
.diff-file-hdr:hover{background:#1e2740}
.diff-file-hdr .fname{color:#93c5fd;flex:1}
.diff-file-hdr .toggle{color:#475569;font-size:.7rem}
.diff-lines{overflow:hidden}
.diff-lines.collapsed{display:none}
.diff-line{font-family:"JetBrains Mono","Fira Code",Menlo,monospace;font-size:.75rem;line-height:1.5;padding:0 16px;white-space:pre;display:flex}
.diff-line.add{background:#0d2615;color:#4ade80}
.diff-line.del{background:#2c0b0b;color:#f87171}
.diff-line.hunk{background:#1a1d27;color:#64748b}
.diff-line.ctx{color:#4b5563}
.diff-line .ln{color:#374151;min-width:26px;margin-right:10px;user-select:none;flex-shrink:0}
.no-diff{padding:20px 18px;font-size:.82rem;color:#475569}
</style>
</head>
<body>

<header>
  <h1>AVD Agent</h1>
  <span>Arista Validated Designs — change control</span>
</header>

<div class="main">

  <!-- ── Left: form ── -->
  <div class="left">
    <div>
      <label for="req">Change request</label>
      <textarea id="req" placeholder="e.g. Add NTP server 2.pool.ntp.org&#10;e.g. Change BGP ASN for spines to 65000"></textarea>
    </div>
    <div>
      <label for="model">Model</label>
      <select id="model"></select>
    </div>
    <div>
      <label for="bf-host">Batfish host</label>
      <input type="text" id="bf-host" value="localhost">
    </div>
    <div style="display:flex;gap:20px">
      <div class="row"><input type="checkbox" id="dry-run"><label for="dry-run">Dry run</label></div>
      <div class="row"><input type="checkbox" id="skip-val"><label for="skip-val">Skip validation</label></div>
    </div>
    <button class="btn btn-run" id="btn-run" onclick="submitRun()">Submit Change</button>
    <div class="lock-msg" id="lock-msg">⚠ Another run is in progress</div>

    <div class="hist">
      <h3>Recent runs</h3>
      <div id="hist-list"><span style="color:#475569;font-size:.75rem">Loading…</span></div>
    </div>
  </div>

  <!-- ── Right: log + approval ── -->
  <div class="right">

    <div class="log-wrap" id="log-wrap">
      <div class="log-hdr">
        <span id="log-title">Agent log</span>
        <span id="branch-tag" class="branch-tag" style="display:none"></span>
      </div>
      <div id="log">
        <div class="ph" id="placeholder">
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" opacity=".3">
            <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>
          </svg>
          <p>Submit a change request to begin</p>
        </div>
      </div>
    </div>

    <!-- Approval / diff panel (hidden until run succeeds) -->
    <div class="approval" id="approval">
      <div class="apr-hdr">
        <span>Device Configuration Changes</span>
        <span class="sub" id="apr-branch"></span>
        <button class="btn-approve" id="btn-approve" onclick="approveRun()">✓ Approve &amp; Merge</button>
        <button class="btn-reject"  id="btn-reject"  onclick="rejectRun()">✗ Reject</button>
      </div>
      <div class="diff-container" id="diff-container">
        <div class="no-diff">Loading diff…</div>
      </div>
    </div>

  </div>
</div>

<script>
let currentRunId = null;
let evtSrc = null;

// ── Model list ────────────────────────────────────────────────────────────────
fetch('/api/models').then(r=>r.json()).then(models=>{
  const sel=document.getElementById('model');
  models.forEach((m,i)=>{
    const o=document.createElement('option');
    o.value=m;o.textContent=m;if(i===0)o.selected=true;
    sel.appendChild(o);
  });
});

// ── History ───────────────────────────────────────────────────────────────────
// Keyed store so onclick handlers can look up run data without embedding JSON in HTML
const _runsCache={};

function loadHistory(){
  fetch('/api/runs').then(r=>r.json()).then(runs=>{
    runs.forEach(r=>{ _runsCache[r.run_id]=r; });
    const el=document.getElementById('hist-list');
    if(!runs.length){el.innerHTML='<span style="color:#475569;font-size:.75rem">No runs yet</span>';return;}
    // Build DOM nodes instead of innerHTML to avoid attribute-quoting issues
    el.innerHTML='';
    runs.forEach(r=>{
      const isPending=r.status==='pending_approval';
      const isApproved=r.status==='approved';
      const isRejected=r.status==='rejected';
      let statusBadge='';
      if(isPending)    statusBadge='<span class="badge" style="background:#713f12;color:#fde68a">PENDING</span>';
      else if(isApproved) statusBadge='<span class="badge ok">APPROVED</span>';
      else if(isRejected) statusBadge='<span class="badge fail">REJECTED</span>';
      else if(r.success===true)  statusBadge='<span class="badge ok">PASS</span>';
      else if(r.success===false) statusBadge='<span class="badge fail">FAIL</span>';
      else statusBadge='<span class="badge pending">—</span>';

      const div=document.createElement('div');
      div.className='hist-item';
      div.innerHTML=`<div class="hist-req">${esc(r.request||'(unknown)')}</div>`
                   +`<div class="hist-meta">${statusBadge} ${esc(r.ts)}</div>`;
      div.addEventListener('click',()=>openRun(r.run_id));
      el.appendChild(div);
    });
  });
}

function openRun(runId){
  const r=_runsCache[runId];
  if(!r)return;
  currentRunId=runId;
  document.getElementById('log-title').textContent='Run: '+r.ts;
  const bt=document.getElementById('branch-tag');
  if(r.branch){bt.textContent=r.branch;bt.style.display='inline';}
  else{bt.style.display='none';}

  document.getElementById('log').innerHTML='<div class="l-info">'+esc(r.request)+'</div>';
  if(evtSrc){evtSrc.close();evtSrc=null;}

  if(r.status==='pending_approval'&&r.branch){
    // Live run awaiting review — show approve/reject controls
    document.getElementById('btn-approve').style.display='';
    document.getElementById('btn-reject').style.display='';
    loadDiff(runId,r.branch,r.validation_warning||false);
  } else if(r.success===true||r.has_diff){
    // Completed run — show diff read-only (may show "not available" for pre-change-control runs)
    loadDiffReadOnly(runId,r.status);
  } else {
    document.getElementById('approval').classList.remove('open');
  }
}

async function loadDiffReadOnly(runId,status){
  const panel=document.getElementById('approval');
  panel.classList.add('open');
  document.getElementById('btn-approve').style.display='none';
  document.getElementById('btn-reject').style.display='none';
  const label=status==='approved'?'✓ Approved':status==='rejected'?'✗ Rejected':'Changes';
  document.getElementById('apr-branch').textContent=label;
  const wb=document.getElementById('val-warning');
  if(wb)wb.style.display='none';
  const res=await fetch(`/api/runs/${runId}/diff`);
  const data=await res.json();
  renderDiff(data.diff);
}
loadHistory();

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function appendLog(cls,icon,msg){
  const d=document.createElement('div');
  d.className=cls;
  d.textContent=(icon?icon+' ':'')+msg;
  const log=document.getElementById('log');
  log.appendChild(d);
  log.scrollTop=log.scrollHeight;
}

// ── Submit ────────────────────────────────────────────────────────────────────
async function submitRun(){
  const req=document.getElementById('req').value.trim();
  if(!req){alert('Please enter a change request.');return;}

  const btn=document.getElementById('btn-run');
  btn.disabled=true;

  // Reset UI
  document.getElementById('log').innerHTML='';
  document.getElementById('approval').classList.remove('open');
  document.getElementById('branch-tag').style.display='none';
  currentRunId=null;
  if(evtSrc){evtSrc.close();evtSrc=null;}

  const body={
    request:         req,
    model:           document.getElementById('model').value,
    dry_run:         document.getElementById('dry-run').checked,
    skip_validation: document.getElementById('skip-val').checked,
    batfish_host:    document.getElementById('bf-host').value.trim()||'localhost',
  };

  let runId;
  try{
    const res=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!res.ok){
      const err=await res.json();
      if(res.status===409){
        document.getElementById('lock-msg').style.display='block';
        setTimeout(()=>document.getElementById('lock-msg').style.display='none',4000);
      }else{
        appendLog('l-fail','✗',err.detail||'Server error');
      }
      btn.disabled=false;return;
    }
    const data=await res.json();
    runId=currentRunId=data.run_id;
  }catch(e){appendLog('l-fail','✗','Could not reach server: '+e);btn.disabled=false;return;}

  // Stream events
  evtSrc=new EventSource('/api/stream/'+runId);
  evtSrc.onmessage=e=>{
    let ev;try{ev=JSON.parse(e.data);}catch{return;}
    const{type,msg}=ev;
    if(type==='banner')    appendLog('l-banner','──',msg);
    else if(type==='step') appendLog('l-step','▶',msg);
    else if(type==='ok')   appendLog('l-ok','✓',msg);
    else if(type==='fail') appendLog('l-fail','✗',msg);
    else if(type==='warn') appendLog('l-warn','!',msg);
    else if(type==='info') appendLog('l-info',' ',msg);
    else if(type==='branch'){
      const tag=document.getElementById('branch-tag');
      tag.textContent=msg;tag.style.display='';
    }
    else if(type==='pending_approval'){
      loadDiff(runId,msg.branch,msg.validation_warning);
    }
    else if(type==='done'){
      const ok=msg&&msg.success;
      appendLog(ok?'l-done-ok':'l-done-fail',ok?'✓':'✗',
        ok?'Validation passed — review changes below before merging to main.'
          :'Run finished with errors.');
      evtSrc.close();evtSrc=null;
      btn.disabled=false;
      loadHistory();
    }
  };
  evtSrc.onerror=()=>{
    appendLog('l-fail','✗','Stream disconnected.');
    evtSrc.close();evtSrc=null;btn.disabled=false;
  };
}

// ── Diff loader ───────────────────────────────────────────────────────────────
async function loadDiff(runId, branch, validationWarning){
  document.getElementById('apr-branch').textContent=branch;
  document.getElementById('approval').classList.add('open');
  document.getElementById('btn-approve').style.display='';
  document.getElementById('btn-reject').style.display='';
  document.getElementById('btn-approve').disabled=false;
  document.getElementById('btn-reject').disabled=false;

  // Show / hide validation warning banner
  let wb=document.getElementById('val-warning');
  if(!wb){
    wb=document.createElement('div');
    wb.id='val-warning';
    wb.style.cssText='background:#7c2d12;color:#fef3c7;padding:8px 14px;font-size:.8rem;border-radius:5px;margin-bottom:10px;display:none';
    wb.textContent='⚠ Automated validation checks did not pass. Review the config diff carefully before approving.';
    document.getElementById('diff-container').before(wb);
  }
  wb.style.display=validationWarning?'block':'none';

  const res=await fetch(`/api/runs/${runId}/diff`);
  const data=await res.json();
  renderDiff(data.diff);
}

function renderDiff(raw){
  const container=document.getElementById('diff-container');
  if(!raw||!raw.trim()){
    container.innerHTML='<div class="no-diff">No device config diff available for this run.<br><span style="font-size:.75rem;color:#334155">Runs created before change control was enabled do not have a saved diff.</span></div>';
    return;
  }

  // Split into per-file blocks
  const fileBlocks=raw.split(/^(?=diff --git )/m).filter(Boolean);
  container.innerHTML='';

  fileBlocks.forEach(block=>{
    const lines=block.split('\n');
    // Extract filename from "diff --git a/... b/..."
    const header=lines[0]||'';
    const fnMatch=header.match(/diff --git a\/.+? b\/(.+)/);
    const fullPath=fnMatch?fnMatch[1]:header;
    // Show just the filename (e.g. "dc1-leaf1a.cfg") without the path prefix
    const fname=fullPath.split('/').pop()||fullPath;

    // Count additions/deletions for badge
    let adds=0,dels=0;
    lines.forEach(l=>{if(l.startsWith('+')&&!l.startsWith('+++'))adds++;else if(l.startsWith('-')&&!l.startsWith('---'))dels++;});

    const fileDiv=document.createElement('div');
    fileDiv.className='diff-file';

    const hdr=document.createElement('div');
    hdr.className='diff-file-hdr';
    hdr.innerHTML=`<span class="fname">${esc(fname)}</span>`
      +`<span style="color:#4ade80;font-size:.7rem">+${adds}</span>`
      +`<span style="color:#f87171;font-size:.7rem;margin-left:6px">-${dels}</span>`
      +`<span class="toggle">▾</span>`;

    const linesDiv=document.createElement('div');
    linesDiv.className='diff-lines';

    // Render lines (skip the first 4 meta lines: diff, index, ---, +++)
    let skip=4;
    lines.forEach(line=>{
      if(skip-->0)return;
      const ld=document.createElement('div');
      if(line.startsWith('@@')){
        ld.className='diff-line hunk';
        ld.textContent=line;
      }else if(line.startsWith('+')){
        ld.className='diff-line add';
        ld.innerHTML=`<span class="ln">+</span>${esc(line.slice(1))}`;
      }else if(line.startsWith('-')){
        ld.className='diff-line del';
        ld.innerHTML=`<span class="ln">-</span>${esc(line.slice(1))}`;
      }else{
        ld.className='diff-line ctx';
        ld.innerHTML=`<span class="ln"> </span>${esc(line.slice(1))}`;
      }
      linesDiv.appendChild(ld);
    });

    // Toggle collapse
    hdr.addEventListener('click',()=>{
      const collapsed=linesDiv.classList.toggle('collapsed');
      hdr.querySelector('.toggle').textContent=collapsed?'▸':'▾';
    });

    fileDiv.appendChild(hdr);
    fileDiv.appendChild(linesDiv);
    container.appendChild(fileDiv);
  });
}

// ── Approve / Reject ──────────────────────────────────────────────────────────
async function approveRun(){
  if(!currentRunId)return;
  const btnA=document.getElementById('btn-approve');
  const btnR=document.getElementById('btn-reject');
  btnA.disabled=btnR.disabled=true;
  btnA.textContent='Merging…';

  try{
    const res=await fetch(`/api/runs/${currentRunId}/approve`,{method:'POST'});
    if(res.ok){
      appendLog('l-done-ok','✓','Change approved and merged to main.');
      document.getElementById('approval').classList.remove('open');
      loadHistory();
    }else{
      const err=await res.json();
      appendLog('l-fail','✗','Merge failed: '+(err.detail||'unknown error'));
      btnA.disabled=btnR.disabled=false;
      btnA.textContent='✓ Approve & Merge';
    }
  }catch(e){
    appendLog('l-fail','✗','Request failed: '+e);
    btnA.disabled=btnR.disabled=false;
    btnA.textContent='✓ Approve & Merge';
  }
}

async function rejectRun(){
  if(!currentRunId)return;
  if(!confirm('Reject this change? The branch will be deleted and the working tree restored to main.'))return;
  const btnA=document.getElementById('btn-approve');
  const btnR=document.getElementById('btn-reject');
  btnA.disabled=btnR.disabled=true;
  btnR.textContent='Rejecting…';

  try{
    const res=await fetch(`/api/runs/${currentRunId}/reject`,{method:'POST'});
    if(res.ok){
      appendLog('l-warn','!','Change rejected. Branch deleted, main restored.');
      document.getElementById('approval').classList.remove('open');
      loadHistory();
    }else{
      const err=await res.json();
      appendLog('l-fail','✗','Reject failed: '+(err.detail||'unknown'));
      btnA.disabled=btnR.disabled=false;
      btnR.textContent='✗ Reject';
    }
  }catch(e){
    appendLog('l-fail','✗','Request failed: '+e);
    btnA.disabled=btnR.disabled=false;
    btnR.textContent='✗ Reject';
  }
}

// Cmd/Ctrl+Enter to submit
document.getElementById('req').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))submitRun();
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _HTML


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="AVD Agent Web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    print(f"AVD Agent UI → http://{a.host}:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
