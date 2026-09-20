"""RobotOps backend: REST + WebSocket API around the agent, tools, monitor and safety layer.

  uvicorn backend.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import readiness
from backend.agent import llm
from backend.agent.graph import Agent
from backend.agent.state import Investigation, Phase
from backend.monitor import Monitor
from backend.ros_tools import logs, registry, supervisor
from backend.ros_tools.client import get_client
from backend.ros_tools.common import manifest
from backend.safety import policies
from backend.safety.approvals import ApprovalError, ApprovalRegistry
from backend.safety.audit import AuditLog

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"
INVESTIGATION_DIR = ROOT / "logs" / "investigations"
FAULTS = ["controller_crash", "lidar_failure", "tf_failure", "topic_misconfig", "node_crash", "random"]


class Hub:
    """Fan-out of events to all connected WebSocket clients. Thread-safe publish."""

    def __init__(self):
        self.queues: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, msg: dict):
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._fanout, msg)

    def _fanout(self, msg):
        for q in list(self.queues):
            if q.qsize() < 500:  # a stuck client must not grow memory without bound
                q.put_nowait(msg)


class State:
    def __init__(self):
        self.client = get_client()
        self.monitor = Monitor(self.client)
        self.approvals = ApprovalRegistry()
        self.audit = AuditLog()
        self.hub = Hub()
        self.investigations: dict[str, Investigation] = {}
        self.current: Investigation | None = None
        self.last_health: dict | None = None
        self.last_graph: dict | None = None
        self.last_health_at: float = 0.0
        self.ros_error: str | None = None
        self.readiness: dict | None = None
        self.preparing: str | None = "starting"      # human-readable step while START DEMO preparation runs


S = State()


async def _poll_loop():
    """Refresh health + graph for the dashboard every 1.5 s. Health transitions and slow cycles are logged
    (logs/backend.log) so an unexpected red card can be explained after the fact."""
    prev: dict[str, str] = {}
    while True:
        t0 = time.monotonic()
        try:
            S.last_health = await asyncio.to_thread(S.monitor.health)
            S.last_graph = await asyncio.to_thread(S.monitor.graph)
            S.last_health_at = time.time()
            S.hub.publish({"type": "system", "health": S.last_health, "graph": S.last_graph, "ts": time.time()})
            cur = {k: v["state"] for k, v in (S.last_health.get("components") or {}).items()}
            if cur != prev and prev:
                changes = {k: f"{prev.get(k)}->{v}: {S.last_health['components'][k]['detail']}" for k, v in cur.items() if prev.get(k) != v}
                print(f"[health {time.strftime('%H:%M:%S')}] overall={S.last_health['overall']} " + "; ".join(f"{k} {c}" for k, c in changes.items()), flush=True)
            prev = cur
        except Exception as e:  # noqa: BLE001
            S.hub.publish({"type": "system_error", "error": str(e)})
        took = time.monotonic() - t0
        if took > 2.5:
            print(f"[health {time.strftime('%H:%M:%S')}] slow poll cycle: {took:.1f}s", flush=True)
        await asyncio.sleep(1.5)


async def _readiness_loop():
    """Cheap readiness snapshot every 4 s for the dashboard chips / READY FOR DEMO banner."""
    while True:
        try:
            res = await readiness.run_preflight(S.client, warm=False, api_url=None, health=S.last_health or {"components": {}})
            res["chips"] = readiness.chips(res["checks"])
            res["preparing"] = S.preparing
            S.readiness = res
            S.hub.publish({"type": "readiness", "readiness": res})
        except Exception as e:  # noqa: BLE001
            S.hub.publish({"type": "system_error", "error": f"readiness: {e}"})
        await asyncio.sleep(4)


async def _prepare(reset: bool = True) -> dict:
    """START DEMO preparation: stop our own heavy jobs, clear stale faults, warm the model, verify everything.
    Uses the real robot and the real model; nothing is simulated."""
    def step(text):
        S.preparing = text
        S.hub.publish({"type": "prepare_step", "text": text})
    try:
        step("Stopping background benchmark/profiler processes")
        stopped = await asyncio.to_thread(readiness.stop_own_heavy_processes)
        if reset:
            step("Resetting robot to a healthy configuration")
            await asyncio.to_thread(supervisor.reset)
            logs.state["since"] = time.time()
            for _ in range(40):                       # wait for DDS discovery of all restarted nodes
                await asyncio.sleep(1.5)
                if not [n for n in manifest()["nodes"] if n not in S.client.node_names()]:
                    break
        step("Warming the language model")
        res = await readiness.run_preflight(S.client, warm=True, api_url=None)
        step("Verifying ROS graph, topics, TF and odometry")
        res["chips"] = readiness.chips(res["checks"])
        res["stopped_processes"] = stopped
        S.readiness = res
        S.hub.publish({"type": "readiness", "readiness": res})
        return res
    finally:
        S.preparing = None
        S.hub.publish({"type": "prepare_step", "text": None})


def _persist(inv: Investigation):
    INVESTIGATION_DIR.mkdir(parents=True, exist_ok=True)
    (INVESTIGATION_DIR / f"{inv.id}.json").write_text(json.dumps(inv.summary(), default=str))


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.hub.loop = asyncio.get_running_loop()
    try:
        S.client.start()
    except Exception as e:  # noqa: BLE001 - backend still serves UI/API with ROS marked unavailable
        S.ros_error = str(e)
    poller = asyncio.create_task(_poll_loop())
    ready_task = asyncio.create_task(_readiness_loop())
    asyncio.create_task(_prepare(reset=False))       # warm the model at startup so the first investigation is fast
    yield
    poller.cancel()
    ready_task.cancel()
    S.client.shutdown()


app = FastAPI(title="RobotOps", lifespan=lifespan)


# ---------------------------------------------------------------------------- status
@app.get("/api/status")
async def status():
    try:
        sup = await asyncio.to_thread(supervisor.status, 2.0)
        sup_ok = True
    except Exception:  # noqa: BLE001
        sup, sup_ok = None, False
    return {"backend": "ok", "ros": {"available": S.client.available, "error": S.ros_error},
            "llm": await llm.status(), "supervisor": {"reachable": sup_ok},
            "investigation_running": bool(S.current and not S.current.done)}


@app.get("/api/readiness")
async def get_readiness():
    return S.readiness or {"ready": False, "reason": "starting", "checks": [], "chips": {}, "preparing": S.preparing}


@app.post("/api/demo/prepare")
async def prepare_demo():
    """START DEMO: clean slate + model warm + full preflight. Returns the readiness result."""
    if S.current and not S.current.done:
        raise HTTPException(409, "an investigation is running")
    if S.preparing:
        raise HTTPException(409, f"already preparing: {S.preparing}")
    S.preparing = "starting"
    res = await _prepare(reset=True)
    S.audit.write(None, "demo_prepared", ready=res["ready"], reason=res.get("reason"))
    S.current = None
    S.hub.publish({"type": "demo_reset", "ts": time.time()})
    return res


@app.get("/api/health")
async def health():
    return S.last_health or await asyncio.to_thread(S.monitor.health)


@app.get("/api/graph")
async def graph():
    return S.last_graph or await asyncio.to_thread(S.monitor.graph)


@app.get("/api/tools")
async def tools():
    return {"read_only": [s["function"] for s in registry.llm_tool_specs()],
            "state_changing": [{"action": a, "description": d, "requires_approval": True}
                               for a, d in policies.ACTIONS.items()],
            "repairable_components": policies.REPAIRABLE}


class ToolRun(BaseModel):
    args: dict = Field(default_factory=dict)


@app.post("/api/tools/{name}")
async def run_tool(name: str, body: ToolRun):
    """Run one read-only diagnostic tool manually (same allowlist as the agent)."""
    res = await asyncio.to_thread(registry.execute, S.client, name, body.args)
    S.audit.write(None, "manual_tool", tool=name, args=body.args, success=res.success)
    return res.model_dump()


# ---------------------------------------------------------------------------- investigations
class InvestigateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


async def _run_investigation(inv: Investigation):
    agent = Agent(S.client, S.approvals, S.audit)
    await agent.run(inv)
    _persist(inv)
    S.hub.publish({"type": "investigation_done", "investigation": inv.summary()})


@app.post("/api/investigations")
async def start_investigation(req: InvestigateRequest):
    if S.current and not S.current.done:
        raise HTTPException(409, "an investigation is already running")
    inv = Investigation(req.query.strip(), listener=S.hub.publish)
    S.investigations[inv.id] = inv
    S.current = inv
    S.hub.publish({"type": "investigation_started", "investigation": inv.summary()})
    asyncio.create_task(_run_investigation(inv))
    return inv.summary()


@app.get("/api/investigations")
async def list_investigations():
    out = [{k: v for k, v in i.summary().items() if k not in ("events", "evidence")}
           for i in S.investigations.values()]
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


@app.get("/api/investigations/current")
async def current_investigation():
    if S.current is None:
        return None
    return S.current.summary()


def _get(inv_id: str) -> Investigation:
    inv = S.investigations.get(inv_id)
    if inv is None:
        raise HTTPException(404, "unknown investigation")
    return inv


@app.get("/api/investigations/{inv_id}")
async def get_investigation(inv_id: str):
    return _get(inv_id).summary()


class Decision(BaseModel):
    proposal_id: str
    operator: str = "operator"


def _decide(inv_id: str, d: Decision, approve: bool):
    inv = _get(inv_id)
    if inv.phase != Phase.AWAITING_APPROVAL or not inv.proposal or inv.proposal["id"] != d.proposal_id:
        raise HTTPException(409, "this investigation is not awaiting approval for that proposal")
    try:
        p = S.approvals.decide(d.proposal_id, approve, by=d.operator[:40])
    except ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    return {"proposal_id": p.id, "state": p.state}


@app.post("/api/investigations/{inv_id}/approve")
async def approve(inv_id: str, d: Decision):
    return _decide(inv_id, d, True)


@app.post("/api/investigations/{inv_id}/reject")
async def reject(inv_id: str, d: Decision):
    return _decide(inv_id, d, False)


# ---------------------------------------------------------------------------- demo control
class InjectRequest(BaseModel):
    fault: Literal["controller_crash", "lidar_failure", "tf_failure", "topic_misconfig", "node_crash", "random"]


@app.get("/api/faults")
async def faults():
    return {"faults": FAULTS}


@app.post("/api/faults/inject")
async def inject(req: InjectRequest):
    """Fault injection for the demo. The injected fault is NOT stored anywhere the agent can read;
    for 'random' the backend itself never learns which fault was chosen."""
    try:
        res = await asyncio.to_thread(supervisor.inject, req.fault)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"demo supervisor unreachable: {e}") from e
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "injection failed"))
    S.audit.write(None, "fault_injected", requested=req.fault)
    shown = "hidden" if req.fault == "random" else req.fault
    S.hub.publish({"type": "fault_injected", "fault": shown, "ts": time.time()})
    return {"ok": True, "injected": shown}


@app.post("/api/demo/reset")
async def reset_demo():
    if S.current and not S.current.done:
        raise HTTPException(409, "an investigation is running")
    try:
        await asyncio.to_thread(supervisor.reset)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"demo supervisor unreachable: {e}") from e
    logs.state["since"] = time.time()   # logs from before the reset belong to a previous incident
    S.audit.write(None, "demo_reset")
    S.hub.publish({"type": "demo_reset", "ts": time.time()})
    return {"ok": True}


@app.get("/api/audit")
async def audit(n: int = 200):
    return S.audit.tail(max(1, min(n, 2000)))


# ---------------------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    S.hub.queues.add(q)
    try:
        await websocket.send_json({"type": "hello", "health": S.last_health, "graph": S.last_graph, "readiness": S.readiness,
                                   "investigation": S.current.summary() if S.current else None})
        while True:
            msg = await q.get()
            await websocket.send_text(json.dumps(msg, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        S.hub.queues.discard(q)


# ---------------------------------------------------------------------------- frontend
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file() and FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
