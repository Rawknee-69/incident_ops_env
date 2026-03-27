from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from incident_ops_env.models import IncidentAction, IncidentObservation
from incident_ops_env.server.metrics import metrics
from incident_ops_env.server.session_manager import SessionManager


app = FastAPI(title="IncidentOpsEnv", version="1.0.0")
session_manager = SessionManager()
PATTERN_TYPE_VALUES = [
    "database_overload",
    "memory_leak",
    "network_partition",
    "deployment_regression",
    "traffic_spike",
    "disk_full",
    "authentication_failure",
    "unknown",
]


class ResetRequest(BaseModel):
    task_id: Literal[1, 2, 3]
    scenario_id: str | None = None
    seed: int | None = None


class ResetResponse(BaseModel):
    observation: IncidentObservation
    session_id: str


class StepRequest(BaseModel):
    action: IncidentAction


class GraderRequest(BaseModel):
    session_id: str = Field(min_length=1)


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
                    "severity_values": ["P1", "P2", "P3"],
                    "pattern_type_values": PATTERN_TYPE_VALUES,
                },
            },
            {
                "task_id": 2,
                "name": "Root Cause Analysis",
                "difficulty": "medium",
                "description": "Query logs and metrics, identify root cause service, propose fix command.",
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
async def reset(payload: ResetRequest, x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> ResetResponse:
    session_id, env = session_manager.create_or_get_session(x_session_id)
    obs = env.reset(task_id=payload.task_id, scenario_id=payload.scenario_id, seed=payload.seed)
    metrics.record_episode_start(session_id, payload.task_id)
    return ResetResponse(observation=obs, session_id=session_id)


@app.post("/step")
async def step(payload: StepRequest, x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> dict[str, Any]:
    if not x_session_id:
        raise HTTPException(status_code=422, detail="X-Session-ID header is required.")
    env = session_manager.get_session(x_session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Unknown session_id. Call /reset first.")
    action = payload.action
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
async def grader(payload: GraderRequest) -> dict[str, Any]:
    session_id = payload.session_id
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

    try:
        return run_baseline_sync()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail="No LLM API key configured.") from exc


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
