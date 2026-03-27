from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import gradio as gr
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

import baseline as baseline_runner
from incident_ops_env.models import IncidentAction, IncidentObservation
from incident_ops_env.server.metrics import metrics, metrics_hub
from incident_ops_env.server.scenario_loader import (
    list_scenarios_for_task,
    registry,
    validate_scenario_data,
)
from incident_ops_env.server.session_manager import SessionManager
from ui import build_gradio_app


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


class ScenarioRequest(BaseModel):
    scenario: dict[str, Any]


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    latency_ms = (time.time() - start) * 1000
    metrics.record_request(request.url.path, latency_ms)
    await metrics_hub.publish(
        {
            "type": "request",
            "path": request.url.path,
            "method": request.method,
            "latency_ms": round(latency_ms, 2),
            "timestamp": time.time(),
        }
    )
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
    try:
        obs = env.reset(task_id=payload.task_id, scenario_id=payload.scenario_id, seed=payload.seed)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metrics.record_episode_start(session_id, payload.task_id)
    await metrics_hub.publish(
        {
            "type": "episode_start",
            "session_id": session_id,
            "task_id": payload.task_id,
            "scenario_id": env.scenario_id,
            "timestamp": time.time(),
        }
    )
    return ResetResponse(observation=obs, session_id=session_id)


@app.post("/step")
async def step(payload: StepRequest, x_session_id: str | None = Header(default=None, alias="X-Session-ID")) -> dict[str, Any]:
    if not x_session_id:
        raise HTTPException(status_code=422, detail="X-Session-ID header is required.")
    env = session_manager.get_session(x_session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Unknown session_id. Call /reset first.")
    action = payload.action
    try:
        result = env.step(action)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    metrics.record_step(x_session_id, action.action_type.value, result.reward, result.observation.last_action_was_valid)
    await metrics_hub.publish(
        {
            "type": "step",
            "session_id": x_session_id,
            "action_type": action.action_type.value,
            "reward": result.reward,
            "done": result.done,
            "valid": result.observation.last_action_was_valid,
            "timestamp": time.time(),
        }
    )
    if result.done:
        episode_score = env.grade()
        metrics.record_episode_end(x_session_id, episode_score, env.step_number)
        await metrics_hub.publish(
            {
                "type": "episode_end",
                "session_id": x_session_id,
                "task_id": env.task_id,
                "score": episode_score,
                "steps": env.step_number,
                "timestamp": time.time(),
            }
        )
    return {
        "observation": result.observation.model_dump(),
        "reward": result.reward,
        "reward_model": result.reward_model.model_dump(),
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
    score = env.grade()
    grader_payload = {
        "score": score,
        "task_id": env.task_id,
        "episode_id": env.episode_id,
        "scenario_id": env.scenario_id,
        "breakdown": env.reward_breakdown,
        "grader_version": "1.0.0",
    }
    await metrics_hub.publish(
        {
            "type": "grader",
            "session_id": session_id,
            "task_id": env.task_id,
            "score": score,
            "timestamp": time.time(),
        }
    )
    return grader_payload


@app.get("/scenarios")
async def scenarios() -> dict[str, Any]:
    built_in: dict[str, list[str]] = {}
    for task_id in (1, 2, 3):
        built_in[f"task_{task_id}"] = [item["scenario_id"] for item in list_scenarios_for_task(task_id, include_uploaded=False)]
    return {
        "built_in": built_in,
        "uploaded": registry.list_uploaded_ids(),
    }


@app.post("/scenarios/validate")
async def validate_scenario(payload: ScenarioRequest) -> dict[str, Any]:
    scenario = validate_scenario_data(payload.scenario)
    return {
        "valid": True,
        "scenario_id": scenario["scenario_id"],
        "task_id": scenario["task_id"],
    }


@app.post("/scenarios/upload")
async def upload_scenario(payload: ScenarioRequest) -> dict[str, Any]:
    scenario = validate_scenario_data(payload.scenario)
    path = registry.save_uploaded_scenario(scenario)
    return {
        "uploaded": True,
        "scenario_id": scenario["scenario_id"],
        "task_id": scenario["task_id"],
        "path": str(path),
    }


@app.post("/baseline")
async def baseline() -> dict:
    try:
        return await baseline_runner.run_baseline()
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail="No LLM API key configured.") from exc


@app.get("/ui", include_in_schema=False)
async def ui_redirect() -> RedirectResponse:
    # Use a relative redirect to avoid proxy/host header mismatches on hosted platforms.
    return RedirectResponse(url="/ui/")


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


@app.websocket("/ws/metrics")
async def metrics_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = metrics_hub.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                await websocket.send_text(
                    json.dumps({"type": "snapshot", "timestamp": time.time(), "payload": metrics.snapshot()})
                )
    except WebSocketDisconnect:
        return
    finally:
        metrics_hub.unsubscribe(queue)


gradio_demo = build_gradio_app()
app = gr.mount_gradio_app(app, gradio_demo, path="/ui")
