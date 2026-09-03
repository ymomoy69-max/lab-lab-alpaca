"""FastAPI dashboard + SSE event stream."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.audit import AuditStore
from agent.config import SETTINGS
from agent.orchestrator import NexusOrchestrator
from agent import runtime_creds

app = FastAPI(title="Nexus Agent", version="1.0.0")
store = AuditStore(SETTINGS.db_path, SETTINGS.audit_dir)
subscribers: list[asyncio.Queue] = []


def _broadcast(type_: str, message: str, symbol: str | None, payload: dict):
    event = {"type": type_, "message": message, "symbol": symbol, "payload": payload}
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except Exception:
            pass


orch = NexusOrchestrator(on_event=_broadcast)


class CredentialsIn(BaseModel):
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    expected_account: str = ""
    armed: bool = False
    gemini_key: str = ""
    openai_key: str = ""


def _require_credentials() -> None:
    if not runtime_creds.status().configured:
        raise HTTPException(
            status_code=401,
            detail="Connect your Alpaca paper keys in the setup form first",
        )


@app.get("/api/credentials")
def get_credentials():
    return runtime_creds.status().as_dict()


@app.post("/api/credentials")
def save_credentials(body: CredentialsIn):
    try:
        result = runtime_creds.apply_credentials(
            api_key=body.api_key,
            api_secret=body.api_secret,
            expected_account=body.expected_account,
            armed=body.armed,
            gemini_key=body.gemini_key,
            openai_key=body.openai_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _broadcast("credentials", f"Connected paper account {result.get('account')}", None, {"armed": result.get("armed")})
    return {**result, **runtime_creds.status().as_dict()}


@app.delete("/api/credentials")
def delete_credentials():
    try:
        orch.stop()
    except Exception:
        pass
    runtime_creds.clear_credentials()
    _broadcast("credentials", "Disconnected — keys cleared from this server", None, {})
    return runtime_creds.status().as_dict()


@app.get("/api/feedback")
def feedback():
    snap = store.latest_feedback_snapshot()
    live = orch.last_feedback
    return live or snap or {"message": "no feedback yet"}


@app.get("/api/portfolio")
def portfolio():
    snap = store.latest_portfolio()
    return snap or {"message": "no snapshots yet"}


@app.post("/api/proof/build")
def build_proof():
    _require_credentials()
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "tools/build_proof.py"], cwd=str(Path(__file__).parent.parent))
    return {"ok": r.returncode == 0}


@app.get("/api/health")
def health():
    creds = runtime_creds.status().as_dict()
    return {
        "ok": True,
        "armed": SETTINGS.armed,
        "paper": SETTINGS.paper,
        "configured": creds["configured"],
        "account": creds.get("account") or "",
        "source": creds.get("source"),
    }


@app.get("/api/events")
def events(limit: int = 100):
    return store.recent_events(limit)


@app.get("/api/decisions")
def decisions(limit: int = 50):
    return store.recent_decisions(limit)


@app.post("/api/agent/run-once")
def run_once():
    _require_credentials()
    orch.run_once()
    return {"ok": True}


@app.post("/api/agent/start")
def start_agent():
    _require_credentials()
    orch.start()
    return {"ok": True, "interval": SETTINGS.tick_seconds}


@app.post("/api/agent/stop")
def stop_agent():
    orch.stop()
    return {"ok": True}


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    subscribers.append(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if q in subscribers:
                subscribers.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    html = static_dir / "index.html"
    if html.exists():
        return FileResponse(html)
    return {"message": "Nexus Agent API — open /api/health"}
