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
from incident_ops_env.models import IncidentAction, IncidentObservation, IncidentState
from incident_ops_env.server.metrics import metrics, metrics_hub
from incident_ops_env.server.scenario_loader import (
    list_scenarios_for_task,
    registry,
    validate_scenario_data,
)
from incident_ops_env.server.session_manager import SessionManager
from ui.gradio_app import WORKBENCH_SCROLL_CSS, build_gradio_app


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


@app.get("/metadata")
async def metadata() -> dict:
    """OpenEnv required endpoint. Returns environment name and description."""
    return {
        "name": "incident_ops_env",
        "description": (
            "Production incident response environment for RL agent training. "
            "Simulates real SRE on-call workflows: alert triage, root cause analysis, "
            "and multi-step runbook execution with escalation and postmortem writing."
        ),
        "version": "1.0.0",
        "author": "Kyoiske",
        "tasks": 3,
        "difficulty_range": ["easy", "medium", "hard"],
        "max_steps_by_task": {"1": 5, "2": 15, "3": 25},
    }


@app.get("/schema")
async def schema() -> dict:
    """OpenEnv required endpoint. Returns JSON schemas for action, observation, and state."""
    return {
        "action": IncidentAction.model_json_schema(),
        "observation": IncidentObservation.model_json_schema(),
        "state": IncidentState.model_json_schema(),
    }


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> dict:
    """
    OpenEnv required endpoint. MCP JSON-RPC 2.0 interface.
    Exposes all 9 environment action types as MCP tools.
    Supports tools/list method. Returns JSON-RPC 2.0 format.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    method = body.get("method", "")
    request_id = body.get("id", 1)

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "classify_alert",
                        "description": (
                            "Task 1 - Alert Triage. Classify the real incident from a noisy alert dump. "
                            "Identify the severity level, the affected service, and the failure pattern type. "
                            "This is the primary action for Task 1 and ends the episode when called."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "severity": {
                                    "type": "string",
                                    "enum": ["P1", "P2", "P3"],
                                    "description": "P1=critical outage, P2=major degradation, P3=minor issue.",
                                },
                                "service_name": {
                                    "type": "string",
                                    "description": "Name of the service the agent identifies as affected.",
                                },
                                "pattern_type": {
                                    "type": "string",
                                    "enum": [
                                        "database_overload",
                                        "memory_leak",
                                        "network_partition",
                                        "deployment_regression",
                                        "traffic_spike",
                                        "disk_full",
                                        "authentication_failure",
                                        "unknown",
                                    ],
                                    "description": "The failure pattern this incident matches.",
                                },
                            },
                            "required": ["severity", "service_name", "pattern_type"],
                        },
                    },
                    {
                        "name": "filter_logs",
                        "description": (
                            "Task 2/3 - Root Cause Analysis. Query the log database for a specific service. "
                            "Returns up to 20 matching log lines. Use this to find error messages and trace "
                            "the root cause. Relevant log queries earn +0.05 reward. "
                            "Irrelevant queries (after 3+) earn -0.02 penalty."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "log_service": {
                                    "type": "string",
                                    "description": (
                                        "Service whose logs to query (e.g. 'checkout-service', "
                                        "'payment-service', 'inventory-service')."
                                    ),
                                },
                                "log_level": {
                                    "type": "string",
                                    "enum": ["ERROR", "WARN", "INFO", "DEBUG"],
                                    "description": "Optional: filter to only this log level. Omit to return all levels.",
                                },
                                "log_keyword": {
                                    "type": "string",
                                    "description": (
                                        "Optional: keyword to search in log messages "
                                        "(e.g. 'NullPointerException', 'timeout', 'No space left')."
                                    ),
                                },
                            },
                            "required": ["log_service"],
                        },
                    },
                    {
                        "name": "get_metric",
                        "description": (
                            "Task 2/3 - Root Cause Analysis. Fetch metric time-series data for a specific service. "
                            "Returns metric snapshots. Use this to confirm anomalies in CPU, memory, error rate, "
                            "disk usage, or latency. service_name is required - cannot be omitted. "
                            "Querying a relevant metric earns +0.05 reward."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "service_name": {
                                    "type": "string",
                                    "description": (
                                        "Name of the service to query metrics for. REQUIRED - "
                                        "must be specified explicitly."
                                    ),
                                },
                                "metric_name": {
                                    "type": "string",
                                    "description": (
                                        "Metric to retrieve. Common values: 'cpu_percent', 'memory_mb', "
                                        "'error_rate', 'disk_percent', 'latency_ms', 'request_count'."
                                    ),
                                },
                                "metric_window_minutes": {
                                    "type": "integer",
                                    "description": "Optional: how many minutes of history to return. Defaults to 60 if omitted.",
                                },
                            },
                            "required": ["service_name", "metric_name"],
                        },
                    },
                    {
                        "name": "identify_service",
                        "description": (
                            "Task 2/3. Declare which service you believe is the root cause of the incident. "
                            "Call this after gathering evidence via filter_logs and get_metric. "
                            "Correct identification earns +0.15 reward and signals your diagnosis to the environment."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "service_name": {
                                    "type": "string",
                                    "description": (
                                        "The service you have identified as the root cause "
                                        "(e.g. 'checkout-service')."
                                    ),
                                },
                            },
                            "required": ["service_name"],
                        },
                    },
                    {
                        "name": "propose_mitigation",
                        "description": (
                            "Task 2. Propose the remediation command to fix the incident. "
                            "This ends the episode - use it only when confident. "
                            "Correct command earns +0.20 reward. "
                            "Incorrect command on the 2nd+ attempt earns -0.10 penalty. "
                            "Example commands: 'kubectl rollout undo deployment/checkout-service', "
                            "'kubectl exec -n prod inventory-0 -- rm -rf /var/log/archive/*'."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": (
                                        "The exact remediation command. Must match the scenario's "
                                        "correct_mitigation_command to earn full reward."
                                    ),
                                },
                            },
                            "required": ["command"],
                        },
                    },
                    {
                        "name": "execute_runbook_step",
                        "description": (
                            "Task 3 - Full Incident Playbook. Execute a step from the active runbook. "
                            "Steps must be completed in order - each step unlocks the next. "
                            "If a step is marked should_fail, the environment will return a failure message - "
                            "respond by calling escalate, not by retrying. "
                            "Correct execution earns +0.10 reward. Wrong command earns -0.05."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "runbook_step_id": {
                                    "type": "string",
                                    "description": (
                                        "ID of the runbook step to execute (e.g. 'step_1', 'step_2'). "
                                        "Must match an available step in runbook_steps."
                                    ),
                                },
                                "command": {
                                    "type": "string",
                                    "description": (
                                        "The command for this runbook step. Must exactly match "
                                        "the step's correct_command to succeed."
                                    ),
                                },
                            },
                            "required": ["runbook_step_id", "command"],
                        },
                    },
                    {
                        "name": "escalate",
                        "description": (
                            "Task 3. Escalate the incident to a specialist team. "
                            "Use this when a runbook step has failed (should_fail=true) and you cannot resolve it yourself. "
                            "Escalating to the correct team earns +0.30 reward. "
                            "Unnecessary escalation (when you should fix it) earns -0.10 to -0.15 penalty. "
                            "Retrying a failed step instead of escalating earns -0.05 per retry."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "escalation_team": {
                                    "type": "string",
                                    "enum": ["database", "networking", "security", "platform", "management"],
                                    "description": (
                                        "The specialist team to escalate to. Must match the expected "
                                        "team for the failing step."
                                    ),
                                },
                                "escalation_reason": {
                                    "type": "string",
                                    "description": "Explanation of why you are escalating. Describe the failed step and what you attempted.",
                                },
                            },
                            "required": ["escalation_team", "escalation_reason"],
                        },
                    },
                    {
                        "name": "write_postmortem",
                        "description": (
                            "Task 3. Write the incident postmortem after all runbook steps are resolved. "
                            "Only available once all steps are completed or escalated - the postmortem_prompt "
                            "field in the observation will become non-null when ready. "
                            "Scored on keyword coverage: must mention affected services, root cause, "
                            "mitigation steps taken, and prevention recommendations. "
                            "Full keyword coverage earns +0.30 reward. Partial coverage earns +0.05 to +0.29."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "postmortem_text": {
                                    "type": "string",
                                    "description": (
                                        "Full postmortem text. Must cover: (1) what happened and root cause, "
                                        "(2) which services were affected, (3) what mitigation steps were taken, "
                                        "(4) how to prevent recurrence. Scored on keyword match against scenario ground truth."
                                    ),
                                },
                            },
                            "required": ["postmortem_text"],
                        },
                    },
                    {
                        "name": "no_op",
                        "description": (
                            "Take no action this step. Earns -0.03 reward (penalty). "
                            "Only use if genuinely stuck - idle steps waste your action budget "
                            "and reduce the chance of a time bonus."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                ]
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


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
async def grader(
    payload: GraderRequest | None = None,
    x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
) -> dict[str, Any]:
    # Backward-compatible support for clients that pass X-Session-ID header only.
    session_id = (payload.session_id if payload is not None else None) or x_session_id
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required in body or X-Session-ID header.")
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
        # In-process ASGI: HTTP to localhost would fan out across uvicorn workers and break
        # session stickiness (reset on worker A, step on worker B -> 404).
        return await baseline_runner.run_baseline(use_asgi_local=True)
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
app = gr.mount_gradio_app(app, gradio_demo, path="/ui", css=WORKBENCH_SCROLL_CSS)
