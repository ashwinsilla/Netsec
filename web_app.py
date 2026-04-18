#!/usr/bin/env python3
"""
AVD Agent — Web UI

Single-file FastAPI app.  Serves a browser UI at http://localhost:8000 so
network engineers can run the agent without touching the CLI.

Usage
─────
  source .venv/bin/activate
  python3 web_app.py              # starts on http://localhost:8000
  python3 web_app.py --port 9000  # custom port

Progress is streamed live via Server-Sent Events.  Each run gets its own
event stream so multiple engineers can work concurrently.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

import avd_agent as _agent

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="AVD Agent", docs_url=None, redoc_url=None)

RUNS_DIR = _agent.RUNS_DIR

# run_id → asyncio.Queue of SSE event strings (None = stream closed)
_queues: dict[str, asyncio.Queue] = {}


# ── Request / response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    request:         str
    model:           str  = _agent.DEFAULT_MODEL
    dry_run:         bool = False
    skip_validation: bool = False
    batfish_host:    str  = "localhost"
    yes:             bool = True   # web UI always skips the confirmation prompt


class RunInfo(BaseModel):
    run_id:    str
    ts:        str
    request:   str
    model:     str
    success:   bool | None
    work_dir:  str


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(event_type: str, data: Any) -> str:
    return f"data: {json.dumps({'type': event_type, 'msg': data})}\n\n"


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/run")
async def start_run(body: RunRequest) -> dict:
    """Start an agent run and return a run_id to stream progress from."""
    api_key = _agent._load_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY not set in .env")

    run_id = uuid.uuid4().hex[:10]
    queue: asyncio.Queue = asyncio.Queue()
    _queues[run_id] = queue

    # Build a minimal args namespace matching what _run_agent expects
    args = argparse.Namespace(
        model           = body.model,
        dry_run         = body.dry_run,
        yes             = True,
        intent_only     = False,
        verbose         = False,
        skip_validation = body.skip_validation,
        batfish_host    = body.batfish_host,
    )

    async def run() -> None:
        # Wire up the emit callback for this task's context
        def emit(event_type: str, msg: str) -> None:
            queue.put_nowait(_sse(event_type, msg))

        token = _agent._web_emit.set(emit)
        try:
            success = await _agent._run_agent(body.request, args)
            queue.put_nowait(_sse("done", {"success": success}))
        except Exception as exc:
            queue.put_nowait(_sse("fail", f"Unhandled error: {exc}"))
            queue.put_nowait(_sse("done", {"success": False}))
        finally:
            _agent._web_emit.reset(token)
            # Leave queue in dict so /stream can drain it; GC after client disconnects

    asyncio.create_task(run())
    return {"run_id": run_id}


@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str) -> StreamingResponse:
    """SSE stream for a running agent task."""
    queue = _queues.get(run_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    async def generate():
        # Send a keepalive comment immediately so the browser opens the stream
        yield ": keepalive\n\n"
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=60)
                yield chunk
                if '"type": "done"' in chunk:
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # prevent proxy/browser timeout

        _queues.pop(run_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs")
async def list_runs() -> list[RunInfo]:
    """Return the 20 most recent agent runs from agent_runs/."""
    runs: list[RunInfo] = []
    if not RUNS_DIR.is_dir():
        return runs

    dirs = sorted(RUNS_DIR.iterdir(), key=lambda p: p.name, reverse=True)[:20]
    for d in dirs:
        manifest = d / "intent.json"
        if not manifest.is_file():
            continue
        try:
            intent = json.loads(manifest.read_text())
            # Look for a passed/failed marker in any attempt's ansible_output
            success: bool | None = None
            for attempt in sorted(d.glob("attempt_*/ansible_output.txt")):
                text = attempt.read_text(errors="ignore")
                if "failed=0" in text and "unreachable=0" in text:
                    success = True
                elif "failed=" in text:
                    success = False
            runs.append(RunInfo(
                run_id   = d.name,
                ts       = d.name,
                request  = intent.get("task_text", ""),
                model    = intent.get("model", ""),
                success  = success,
                work_dir = str(d),
            ))
        except Exception:
            continue
    return runs


@app.get("/api/models")
async def list_models() -> list[str]:
    return [
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.4",
        "google/gemini-3.1-pro-preview",
    ]


# ── Embedded HTML UI ──────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AVD Agent</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    background: #1a1d27;
    border-bottom: 1px solid #2d3148;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 1.1rem; font-weight: 600; color: #a5b4fc; letter-spacing: .03em; }
  header span { font-size: .8rem; color: #64748b; }

  .container { display: flex; flex: 1; gap: 0; overflow: hidden; }

  /* ── Left panel ── */
  .panel-left {
    width: 380px;
    min-width: 320px;
    background: #1a1d27;
    border-right: 1px solid #2d3148;
    display: flex;
    flex-direction: column;
    padding: 20px;
    gap: 16px;
    overflow-y: auto;
  }

  label { font-size: .8rem; color: #94a3b8; display: block; margin-bottom: 5px; }

  textarea {
    width: 100%;
    min-height: 110px;
    background: #0f1117;
    border: 1px solid #2d3148;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: .9rem;
    padding: 10px 12px;
    resize: vertical;
    outline: none;
    transition: border-color .15s;
  }
  textarea:focus { border-color: #6366f1; }

  select, input[type=text] {
    width: 100%;
    background: #0f1117;
    border: 1px solid #2d3148;
    border-radius: 6px;
    color: #e2e8f0;
    font-size: .85rem;
    padding: 8px 10px;
    outline: none;
  }
  select:focus, input[type=text]:focus { border-color: #6366f1; }

  .row { display: flex; align-items: center; gap: 10px; }
  .row label { margin: 0; cursor: pointer; }
  input[type=checkbox] { accent-color: #6366f1; width: 14px; height: 14px; cursor: pointer; }

  .btn-run {
    width: 100%;
    padding: 10px;
    background: #6366f1;
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: .95rem;
    font-weight: 600;
    cursor: pointer;
    transition: background .15s, opacity .15s;
  }
  .btn-run:hover { background: #4f46e5; }
  .btn-run:disabled { opacity: .5; cursor: not-allowed; }

  /* History list */
  .history h3 { font-size: .8rem; color: #64748b; text-transform: uppercase; letter-spacing: .06em; margin-bottom: 10px; }
  .history-item {
    padding: 8px 10px;
    border-radius: 5px;
    background: #0f1117;
    border: 1px solid #2d3148;
    margin-bottom: 6px;
    font-size: .78rem;
    cursor: pointer;
  }
  .history-item:hover { border-color: #6366f1; }
  .hist-req { color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hist-meta { color: #475569; margin-top: 3px; }
  .badge { display: inline-block; border-radius: 3px; padding: 1px 5px; font-size: .7rem; font-weight: 600; margin-right: 4px; }
  .badge-ok   { background: #14532d; color: #86efac; }
  .badge-fail { background: #450a0a; color: #fca5a5; }
  .badge-run  { background: #1e1b4b; color: #a5b4fc; }

  /* ── Right panel — log ── */
  .panel-right {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .log-header {
    padding: 12px 20px;
    border-bottom: 1px solid #2d3148;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: .85rem;
    color: #64748b;
    background: #1a1d27;
  }
  .log-header .run-id { font-family: monospace; color: #94a3b8; }

  #log {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
    font-family: "JetBrains Mono", "Fira Code", "Menlo", monospace;
    font-size: .82rem;
    line-height: 1.7;
    background: #0f1117;
  }

  .log-banner {
    margin: 12px 0 6px;
    color: #818cf8;
    font-weight: 700;
    border-bottom: 1px solid #2d3148;
    padding-bottom: 4px;
  }
  .log-step { color: #38bdf8; }
  .log-ok   { color: #4ade80; }
  .log-fail { color: #f87171; }
  .log-warn { color: #fbbf24; }
  .log-info { color: #64748b; }
  .log-done-ok   { color: #4ade80; font-weight: 700; margin-top: 10px; }
  .log-done-fail { color: #f87171; font-weight: 700; margin-top: 10px; }

  .placeholder {
    color: #334155;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 8px;
  }
  .placeholder svg { opacity: .3; }
  .placeholder p { font-size: .85rem; }
</style>
</head>
<body>

<header>
  <h1>AVD Agent</h1>
  <span>Arista Validated Designs — natural-language configuration</span>
</header>

<div class="container">

  <!-- Left: form + history -->
  <div class="panel-left">

    <div>
      <label for="req">Change request</label>
      <textarea id="req" placeholder="e.g. Change p2p_uplinks_mtu from 1500 to 9214"></textarea>
    </div>

    <div>
      <label for="model">Model</label>
      <select id="model"></select>
    </div>

    <div>
      <label for="bf-host">Batfish host</label>
      <input type="text" id="bf-host" value="localhost">
    </div>

    <div style="display:flex; gap:20px">
      <div class="row">
        <input type="checkbox" id="dry-run">
        <label for="dry-run">Dry run</label>
      </div>
      <div class="row">
        <input type="checkbox" id="skip-val">
        <label for="skip-val">Skip validation</label>
      </div>
    </div>

    <button class="btn-run" id="btn-run" onclick="submitRun()">Run Agent</button>

    <div class="history" id="history-panel">
      <h3>Recent runs</h3>
      <div id="history-list"><span style="color:#475569;font-size:.78rem">Loading…</span></div>
    </div>

  </div>

  <!-- Right: live log -->
  <div class="panel-right">
    <div class="log-header">
      <span>Output</span>
      <span class="run-id" id="run-id-label"></span>
    </div>
    <div id="log">
      <div class="placeholder">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>
        </svg>
        <p>Submit a request to see live output here</p>
      </div>
    </div>
  </div>

</div>

<script>
const log = document.getElementById('log');
const btn = document.getElementById('btn-run');

// Load model list
fetch('/api/models').then(r => r.json()).then(models => {
  const sel = document.getElementById('model');
  models.forEach((m, i) => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    if (i === 0) opt.selected = true;
    sel.appendChild(opt);
  });
});

// Load history
function loadHistory() {
  fetch('/api/runs').then(r => r.json()).then(runs => {
    const el = document.getElementById('history-list');
    if (!runs.length) { el.innerHTML = '<span style="color:#475569;font-size:.78rem">No runs yet</span>'; return; }
    el.innerHTML = runs.map(r => `
      <div class="history-item" title="${r.work_dir}">
        <div class="hist-req">${escHtml(r.request || '(unknown)')}</div>
        <div class="hist-meta">
          ${r.success === true  ? '<span class="badge badge-ok">PASS</span>'  : ''}
          ${r.success === false ? '<span class="badge badge-fail">FAIL</span>' : ''}
          ${r.success === null  ? '<span class="badge badge-run">—</span>'  : ''}
          ${escHtml(r.ts)}
        </div>
      </div>`).join('');
  });
}
loadHistory();

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function appendLog(cls, icon, msg) {
  const div = document.createElement('div');
  div.className = cls;
  div.textContent = (icon ? icon + ' ' : '') + msg;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function submitRun() {
  const req = document.getElementById('req').value.trim();
  if (!req) { alert('Please enter a change request.'); return; }

  btn.disabled = true;
  log.innerHTML = '';

  const body = {
    request:         req,
    model:           document.getElementById('model').value,
    dry_run:         document.getElementById('dry-run').checked,
    skip_validation: document.getElementById('skip-val').checked,
    batfish_host:    document.getElementById('bf-host').value.trim() || 'localhost',
  };

  let runId;
  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json();
      appendLog('log-fail', '✗', err.detail || 'Server error');
      btn.disabled = false;
      return;
    }
    const data = await res.json();
    runId = data.run_id;
  } catch(e) {
    appendLog('log-fail', '✗', 'Could not reach server: ' + e);
    btn.disabled = false;
    return;
  }

  document.getElementById('run-id-label').textContent = 'run: ' + runId;

  const evtSrc = new EventSource('/api/stream/' + runId);

  evtSrc.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }

    const { type, msg } = ev;

    if (type === 'banner') {
      appendLog('log-banner', '──', msg);
    } else if (type === 'step') {
      appendLog('log-step', '▶', msg);
    } else if (type === 'ok') {
      appendLog('log-ok', '✓', msg);
    } else if (type === 'fail') {
      appendLog('log-fail', '✗', msg);
    } else if (type === 'warn') {
      appendLog('log-warn', '!', msg);
    } else if (type === 'info') {
      appendLog('log-info', ' ', msg);
    } else if (type === 'done') {
      const success = msg && msg.success;
      appendLog(
        success ? 'log-done-ok' : 'log-done-fail',
        success ? '✓' : '✗',
        success ? 'Done — change applied successfully.' : 'Run finished with errors.',
      );
      evtSrc.close();
      btn.disabled = false;
      loadHistory();
    }
  };

  evtSrc.onerror = () => {
    appendLog('log-fail', '✗', 'Stream disconnected.');
    evtSrc.close();
    btn.disabled = false;
  };
}

// Allow Ctrl+Enter to submit
document.getElementById('req').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submitRun();
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
    p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    args = p.parse_args()
    print(f"AVD Agent UI → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
