from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from incident_ops_env.models import IncidentAction
from incident_ops_env.server.metrics import metrics
from incident_ops_env.server.session_manager import SessionManager


app = FastAPI(title="IncidentOpsEnv", version="1.0.0")
session_manager = SessionManager()


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    metrics.record_request(request.url.path, (time.time() - start) * 1000)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "message": str(exc), "path": str(request.url.path)},
    )


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    page_path = Path(__file__).resolve().parent / "static" / "index.html"
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "environment": "incident_ops_env", "version": "1.0.0"}


@app.get("/tasks")
async def tasks() -> dict:
    return {
        "tasks": [
            {
                "task_id": 1,
                "name": "Alert Triage",
                "difficulty": "easy",
                "description": "Classify the real incident from a noisy alert dump.",
                "max_steps": 5,
                "action_schema": {
                    "required_fields": ["action_type", "severity", "service_name", "pattern_type"],
                    "action_type_values": ["classify_alert", "no_op"],
                },
            },
            {
                "task_id": 2,
                "name": "Root Cause Analysis",
                "difficulty": "medium",
                "description": "Query logs and metrics, identify root cause service, propose fix.",
                "max_steps": 15,
                "action_schema": {
                    "required_fields_by_action": {
                        "filter_logs": ["action_type", "log_service"],
                        "get_metric": ["action_type", "metric_name"],
                        "identify_service": ["action_type", "service_name"],
                        "propose_mitigation": ["action_type", "command"],
                    }
                },
            },
            {
                "task_id": 3,
                "name": "Full Incident Playbook",
                "difficulty": "hard",
                "description": "Execute runbook, handle failures, escalate, and write postmortem.",
                "max_steps": 25,
                "action_schema": {
                    "required_fields_by_action": {
                        "execute_runbook_step": ["action_type", "runbook_step_id", "command"],
                        "escalate": ["action_type", "escalation_team", "escalation_reason"],
                        "write_postmortem": ["action_type", "postmortem_text"],
                    }
                },
            },
        ]
    }


@app.post("/reset")
async def reset(payload: dict, x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> dict:
    task_id = payload.get("task_id")
    if task_id not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="task_id must be one of [1, 2, 3]")
    scenario_id = payload.get("scenario_id")
    seed = payload.get("seed")
    session_id, env = session_manager.create_or_get_session(x_session_id)
    obs = env.reset(task_id=task_id, scenario_id=scenario_id, seed=seed)
    metrics.record_episode_start(session_id, task_id)
    return {"observation": obs.model_dump(), "session_id": session_id}


@app.post("/step")
async def step(payload: dict, x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> dict:
    if not x_session_id:
        raise HTTPException(status_code=422, detail="X-Session-ID header is required.")
    env = session_manager.get_session(x_session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Unknown session_id. Call /reset first.")
    action = IncidentAction(**payload["action"])
    result = env.step(action)
    metrics.record_step(x_session_id, action.action_type.value, result.reward, result.observation.last_action_was_valid)
    if result.done:
        metrics.record_episode_end(x_session_id, env.grade(), env.step_number)
    return {
        "observation": result.observation.model_dump(),
        "reward": result.reward,
        "done": result.done,
        "info": result.info,
    }


@app.get("/state")
async def state(x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> dict:
    if not x_session_id:
        raise HTTPException(status_code=422, detail="X-Session-ID header is required.")
    env = session_manager.get_session(x_session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Unknown session_id. Call /reset first.")
    return env.state().model_dump()


@app.post("/grader")
async def grader(payload: dict) -> dict:
    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required.")
    env = session_manager.get_session(session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Unknown session_id.")
    if not env.is_done:
        raise HTTPException(status_code=409, detail="Episode not complete. Finish episode before grading.")
    return {
        "score": env.grade(),
        "task_id": env.task_id,
        "episode_id": env.episode_id,
        "scenario_id": env.scenario_id,
        "breakdown": env.reward_breakdown,
        "grader_version": "1.0.0",
    }


@app.post("/baseline")
async def baseline() -> dict:
    from baseline import run_baseline_sync

    return run_baseline_sync()


@app.get("/metrics")
async def get_metrics() -> dict:
    return metrics.snapshot()


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus() -> str:
    snap = metrics.snapshot()
    lines = [
        "# HELP incident_ops_episodes_total Total episodes started",
        "# TYPE incident_ops_episodes_total counter",
        f"incident_ops_episodes_total {snap['episodes']['started']}",
        "# HELP incident_ops_active_sessions Current active sessions",
        "# TYPE incident_ops_active_sessions gauge",
        f"incident_ops_active_sessions {snap['server']['active_sessions']}",
    ]
    return "\n".join(lines) + "\n"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    _, env = session_manager.create_or_get_session(session_id)
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            msg_type = message.get("type")
            if msg_type == "reset":
                obs = env.reset(task_id=int(message.get("task_id", 1)), seed=message.get("seed"))
                await websocket.send_text(
                    json.dumps({"type": "reset_result", "session_id": session_id, "observation": obs.model_dump()})
                )
            elif msg_type == "step":
                action = IncidentAction(**message["action"])
                result = env.step(action)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "step_result",
                            "observation": result.observation.model_dump(),
                            "reward": result.reward,
                            "done": result.done,
                            "info": result.info,
                        }
                    )
                )
            elif msg_type == "state":
                await websocket.send_text(json.dumps({"type": "state_result", **env.state().model_dump()}))
    except WebSocketDisconnect:
        return
