from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import gradio as gr
import httpx


LOG_LEVEL = os.environ.get("UI_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("incident_ops_ui")

# Scrollable workbench: tab body max-height + Code/Textbox line caps (Gradio scrolls inside editors).
# Passed to ``gr.mount_gradio_app(..., css=...)`` (Gradio 6: avoid ``css`` on ``Blocks()``).
WORKBENCH_SCROLL_CSS = """
.workbench-tab-scroll {
  max-height: min(72vh, 900px);
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 0.4rem;
  box-sizing: border-box;
}
"""
_CODE_PANEL = {"lines": 8, "max_lines": 22}
_STATUS_BOX = {"lines": 1, "max_lines": 5}


def _backend_base_url() -> str:
    return os.environ.get("UI_BACKEND_URL", "http://127.0.0.1:7860")


def _should_use_in_process_api() -> bool:
    """Call the FastAPI app via ASGI in-process when Gradio runs inside the same uvicorn process.

    Loopback HTTP (127.0.0.1:7860) fans out to random workers when ``--workers`` > 1, which breaks
    session stickiness (reset/step/grader) and breaks ``/baseline`` when it used HTTP internally.
    """
    if os.environ.get("UI_USE_IN_PROCESS_API", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    backend = _backend_base_url().rstrip("/").lower()
    return backend in ("http://127.0.0.1:7860", "http://localhost:7860")


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 60.0,
) -> dict:
    started_at = time.perf_counter()
    logger.debug("HTTP request start method=%s path=%s timeout=%s", method, path, timeout_seconds)

    if _should_use_in_process_api():
        from incident_ops_env.server.app import app

        transport = httpx.ASGITransport(app=app)
        client_cm = httpx.Client(transport=transport, base_url="http://local", timeout=timeout_seconds)
        request_target = path
    else:
        client_cm = httpx.Client(timeout=timeout_seconds)
        request_target = _backend_base_url().rstrip("/") + path

    with client_cm as client:
        try:
            response = client.request(method=method, url=request_target, json=payload, headers=headers)
        except httpx.RequestError as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.error(
                "HTTP request error method=%s path=%s elapsed_ms=%s error=%s",
                method,
                path,
                elapsed_ms,
                repr(exc),
            )
            raise
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "HTTP response method=%s path=%s status=%s elapsed_ms=%s",
            method,
            path,
            response.status_code,
            elapsed_ms,
        )
        response.raise_for_status()
        return response.json()


def run_reset(task_id: int, scenario_id: str, seed: int | None) -> tuple[str, str, str]:
    body: dict[str, Any] = {"task_id": int(task_id)}
    if scenario_id.strip():
        body["scenario_id"] = scenario_id.strip()
    if seed is not None:
        body["seed"] = int(seed)
    logger.info("Reset requested task_id=%s scenario_id=%s seed=%s", task_id, scenario_id.strip(), seed)
    try:
        payload = _request("POST", "/reset", body)
        return (
            payload["session_id"],
            json.dumps(payload["observation"], indent=2),
            json.dumps({"status": "reset_complete"}, indent=2),
        )
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        logger.warning("Reset HTTP status error status=%s detail=%s", exc.response.status_code, detail)
        return "", "", f"Reset failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        logger.error("Reset request/network error: %s", repr(exc))
        return "", "", f"Reset request/network error: {exc}"


def run_step(session_id: str, action_json: str) -> tuple[str, str, str]:
    if not session_id.strip():
        return "", "", "Missing session_id. Run reset first."
    try:
        action_payload = json.loads(action_json)
    except json.JSONDecodeError as exc:
        return "", "", f"Invalid action JSON: {exc}"
    logger.info("Step requested session_id=%s", session_id.strip())
    try:
        payload = _request(
            "POST",
            "/step",
            {"action": action_payload},
            headers={"X-Session-ID": session_id.strip()},
        )
        return (
            json.dumps(payload["observation"], indent=2),
            json.dumps(
                {
                    "reward": payload["reward"],
                    "reward_model": payload.get("reward_model", {}),
                    "done": payload["done"],
                    "info": payload.get("info", {}),
                },
                indent=2,
            ),
            "step_complete",
        )
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        logger.warning(
            "Step HTTP status error session_id=%s status=%s detail=%s",
            session_id.strip(),
            exc.response.status_code,
            detail,
        )
        status = f"Step failed ({exc.response.status_code}): {detail}"
        if exc.response.status_code == 422:
            status = (
                "Step validation failed (422). Check `Action JSON` matches task schema "
                f"and required fields.\nServer detail: {detail}"
            )
        return "", "", status
    except httpx.RequestError as exc:
        logger.error("Step request error session_id=%s error=%s", session_id.strip(), repr(exc))
        return "", "", f"Step request/network error: {exc}"


def fetch_state(session_id: str) -> str:
    if not session_id.strip():
        return "Missing session_id."
    try:
        payload = _request("GET", "/state", headers={"X-Session-ID": session_id.strip()})
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"State request failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"State request/network error: {exc}"


def fetch_tasks() -> str:
    try:
        payload = _request("GET", "/tasks")
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"Tasks request failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"Tasks request/network error: {exc}"


def fetch_metrics() -> str:
    try:
        payload = _request("GET", "/metrics")
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"Metrics request failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"Metrics request/network error: {exc}"


def run_baseline() -> str:
    logger.info("Baseline run requested from Gradio UI")
    try:
        # Baseline can take longer because it runs all tasks and calls an LLM provider.
        payload = _request("POST", "/baseline", timeout_seconds=240.0)
        logger.info("Baseline run completed successfully")
        return json.dumps(payload, indent=2)
    except httpx.ReadTimeout as exc:
        logger.warning("Baseline read timeout: %s", repr(exc))
        return (
            "Baseline request timed out in the UI (240s). "
            "The backend may still be processing, or the provider is too slow."
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Baseline HTTP status error status=%s detail=%s",
            exc.response.status_code,
            exc.response.text,
        )
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        if exc.response.status_code == 503:
            return (
                "Baseline unavailable. Ensure runtime env has `GEMINI_API_KEY` or `OPENAI_API_KEY` "
                "and restart server with `uv run --env-file .env ...`.\n"
                f"Server detail: {detail}"
            )
        return f"Request failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        logger.error("Baseline request error: %s", repr(exc))
        return f"Network/request error calling baseline endpoint: {exc}"


def run_grader(session_id: str) -> str:
    if not session_id.strip():
        return "Missing session_id."
    logger.info("Grader run requested session_id=%s", session_id.strip())
    try:
        payload = _request("POST", "/grader", {"session_id": session_id.strip()})
        logger.info("Grader run completed session_id=%s", session_id.strip())
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Grader HTTP status error session_id=%s status=%s detail=%s",
            session_id.strip(),
            exc.response.status_code,
            exc.response.text,
        )
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        if exc.response.status_code == 409:
            return (
                "Episode not complete yet. Run one or more `/step` actions until `done=true`, "
                f"then retry grading.\nServer detail: {detail}"
            )
        return f"Request failed ({exc.response.status_code}): {detail}"


def upload_scenario(file_obj) -> str:
    if file_obj is None:
        return "No file selected."
    try:
        with open(file_obj.name, encoding="utf-8") as handle:
            content = json.load(handle)
        payload = _request("POST", "/scenarios/upload", {"scenario": content})
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"Upload failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"Upload request/network error: {exc}"


def validate_scenario(file_obj) -> str:
    if file_obj is None:
        return "No file selected."
    try:
        with open(file_obj.name, encoding="utf-8") as handle:
            content = json.load(handle)
        payload = _request("POST", "/scenarios/validate", {"scenario": content})
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"Validation failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"Validation request/network error: {exc}"


def list_scenarios() -> str:
    try:
        payload = _request("GET", "/scenarios")
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"Scenario listing failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return f"Scenario listing request/network error: {exc}"


def metrics_stream_info() -> str:
    base = _backend_base_url().rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws/metrics"
    return f"Live metrics stream endpoint: {ws_url}"


def build_gradio_app() -> gr.Blocks:
    with gr.Blocks(title="IncidentOpsEnv Observability UI") as demo:
        gr.Markdown("# IncidentOpsEnv - Gradio Observability")
        gr.Markdown("Use this UI to inspect episodes, backend metrics, graders, baseline runs, and scenario uploads.")
        gr.Markdown("## Environment Workbench")

        with gr.Tab("Episode Runner"):
            with gr.Column(elem_classes=["workbench-tab-scroll"]):
                with gr.Row():
                    task_id = gr.Dropdown(choices=[1, 2, 3], value=1, label="Task ID")
                    scenario_id = gr.Textbox(label="Scenario ID (optional)")
                    seed = gr.Number(label="Seed (optional)", precision=0)
                reset_btn = gr.Button("Reset Episode")
                session_id = gr.Textbox(label="Session ID")
                observation_box = gr.Code(label="Observation", language="json", **_CODE_PANEL)
                reset_status = gr.Textbox(label="Reset Status", **_STATUS_BOX)

                gr.Markdown("### Step with JSON action")
                action_json = gr.Code(
                    value='{\n  "action_type": "no_op"\n}',
                    language="json",
                    label="Action JSON",
                    **_CODE_PANEL,
                )
                step_btn = gr.Button("Run Step")
                step_observation = gr.Code(label="Observation After Step", language="json", **_CODE_PANEL)
                step_meta = gr.Code(label="Reward/Done/Info", language="json", **_CODE_PANEL)
                step_status = gr.Textbox(label="Step Status", **_STATUS_BOX)
                state_btn = gr.Button("Fetch State")
                state_box = gr.Code(label="Current State", language="json", **_CODE_PANEL)

                reset_btn.click(run_reset, [task_id, scenario_id, seed], [session_id, observation_box, reset_status])
                step_btn.click(run_step, [session_id, action_json], [step_observation, step_meta, step_status])
                state_btn.click(fetch_state, [session_id], [state_box])

        with gr.Tab("Tasks + Grader"):
            with gr.Column(elem_classes=["workbench-tab-scroll"]):
                tasks_btn = gr.Button("Load /tasks")
                tasks_box = gr.Code(label="Tasks", language="json", **_CODE_PANEL)
                grader_btn = gr.Button("Run /grader for Session")
                grader_box = gr.Code(label="Grader Result", language="json", **_CODE_PANEL)
                tasks_btn.click(fetch_tasks, None, [tasks_box])
                grader_btn.click(run_grader, [session_id], [grader_box])

        with gr.Tab("Metrics"):
            with gr.Column(elem_classes=["workbench-tab-scroll"]):
                metrics_btn = gr.Button("Refresh Metrics")
                metrics_box = gr.Code(label="/metrics snapshot", language="json", **_CODE_PANEL)
                ws_info = gr.Textbox(
                    label="Live Stream Endpoint",
                    value=metrics_stream_info(),
                    lines=2,
                    max_lines=4,
                )
                metrics_btn.click(fetch_metrics, None, [metrics_box])
                gr.Timer(2.0).tick(fetch_metrics, None, [metrics_box])

        with gr.Tab("Baseline"):
            with gr.Column(elem_classes=["workbench-tab-scroll"]):
                baseline_btn = gr.Button("Run /baseline")
                baseline_box = gr.Code(label="Baseline Result", language="json", **_CODE_PANEL)
                baseline_btn.click(run_baseline, None, [baseline_box])

        with gr.Tab("Scenario Upload/Validation"):
            with gr.Column(elem_classes=["workbench-tab-scroll"]):
                file_input = gr.File(label="Upload scenario JSON")
                validate_btn = gr.Button("Validate Scenario")
                validate_out = gr.Code(label="Validation Result", language="json", **_CODE_PANEL)
                upload_btn = gr.Button("Upload Scenario")
                upload_out = gr.Code(label="Upload Result", language="json", **_CODE_PANEL)
                list_btn = gr.Button("List Scenarios")
                list_out = gr.Code(label="Scenario Registry", language="json", **_CODE_PANEL)
                validate_btn.click(validate_scenario, [file_input], [validate_out])
                upload_btn.click(upload_scenario, [file_input], [upload_out])
                list_btn.click(list_scenarios, None, [list_out])
    return demo
