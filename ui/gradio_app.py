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


def _format_reward_timeline(entries: list | None) -> str:
    """Human-readable episode reward history (gains vs losses)."""
    if not entries:
        return "// No reward events yet. Run /step after /reset to record gains and losses."
    lines: list[str] = []
    for e in entries:
        r = float(e.get("reward", 0.0))
        kind = "GAIN" if r >= 0 else "LOSS"
        ok = "valid" if e.get("action_was_valid", True) else "INVALID"
        lines.append(
            f"step {e.get('step_number')} | {e.get('action_type')} | {ok} | "
            f"{kind} {r:+.4f} | cumulative {float(e.get('total_reward_so_far', 0.0)):.4f}"
        )
    return "\n".join(lines)


def _observation_context_json(obs: dict[str, Any] | None) -> str:
    o = obs or {}
    subset = {
        "recent_logs": o.get("recent_logs", []),
        "current_metrics": o.get("current_metrics", []),
        "runbook_steps": o.get("runbook_steps", []),
        "last_action_result": o.get("last_action_result"),
        "last_action_was_valid": o.get("last_action_was_valid"),
        "postmortem_prompt": o.get("postmortem_prompt"),
        "actions_remaining": o.get("actions_remaining"),
    }
    return json.dumps(subset, indent=2)


def _format_server_reward_stream(snapshot: dict) -> str:
    rows = (snapshot.get("steps") or {}).get("reward_stream") or []
    if not rows:
        return "// No server-wide reward events yet. Steps appear here after each /step API call."
    lines: list[str] = []
    for row in rows[-50:]:
        ts = row.get("ts", 0)
        gl = row.get("gain_or_loss", "gain")
        v = "OK" if row.get("valid") else "INV"
        lines.append(
            f"t={ts} | {row.get('session')} | {row.get('action_type')} | {v} | "
            f"{gl} {float(row.get('reward', 0)):+.4f}"
        )
    return "\n".join(lines)


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


def run_reset(task_id: int, scenario_id: str, seed: int | None) -> tuple[str, str, str, str, str]:
    body: dict[str, Any] = {"task_id": int(task_id)}
    if scenario_id.strip():
        body["scenario_id"] = scenario_id.strip()
    if seed is not None:
        body["seed"] = int(seed)
    logger.info("Reset requested task_id=%s scenario_id=%s seed=%s", task_id, scenario_id.strip(), seed)
    try:
        payload = _request("POST", "/reset", body)
        obs = payload.get("observation") or {}
        return (
            payload["session_id"],
            json.dumps(obs, indent=2),
            json.dumps({"status": "reset_complete"}, indent=2),
            _format_reward_timeline(obs.get("reward_history")),
            _observation_context_json(obs if isinstance(obs, dict) else None),
        )
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        logger.warning("Reset HTTP status error status=%s detail=%s", exc.response.status_code, detail)
        return "", "", f"Reset failed ({exc.response.status_code}): {detail}", "// Reset failed.", "{}"
    except httpx.RequestError as exc:
        logger.error("Reset request/network error: %s", repr(exc))
        return "", "", f"Reset request/network error: {exc}", "// Reset failed.", "{}"


def run_step(session_id: str, action_json: str) -> tuple[str, str, str, str, str]:
    if not session_id.strip():
        return "", "", "Missing session_id. Run reset first.", "// No session.", "{}"
    try:
        action_payload = json.loads(action_json)
    except json.JSONDecodeError as exc:
        return "", "", f"Invalid action JSON: {exc}", "// Invalid JSON.", "{}"
    logger.info("Step requested session_id=%s", session_id.strip())
    try:
        payload = _request(
            "POST",
            "/step",
            {"action": action_payload},
            headers={"X-Session-ID": session_id.strip()},
        )
        obs = payload.get("observation") or {}
        return (
            json.dumps(obs, indent=2),
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
            _format_reward_timeline(obs.get("reward_history")),
            _observation_context_json(obs if isinstance(obs, dict) else None),
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
        return "", "", status, "// Step failed.", "{}"
    except httpx.RequestError as exc:
        logger.error("Step request error session_id=%s error=%s", session_id.strip(), repr(exc))
        return "", "", f"Step request/network error: {exc}", "// Network error.", "{}"


def fetch_state(session_id: str) -> tuple[str, str, str]:
    if not session_id.strip():
        return "Missing session_id.", "// Missing session_id.", "{}"
    try:
        payload = _request("GET", "/state", headers={"X-Session-ID": session_id.strip()})
        excerpt = payload.get("observation_excerpt") or {}
        return (
            json.dumps(payload, indent=2),
            _format_reward_timeline(payload.get("reward_history")),
            json.dumps(excerpt, indent=2),
        )
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text
        return f"State request failed ({exc.response.status_code}): {detail}", "// State failed.", "{}"
    except httpx.RequestError as exc:
        return f"State request/network error: {exc}", "// Network error.", "{}"


def fetch_reward_stream() -> str:
    try:
        data = _request("GET", "/metrics")
        return _format_server_reward_stream(data)
    except httpx.HTTPStatusError as exc:
        return f"// reward_stream error: {exc.response.status_code}"
    except httpx.RequestError as exc:
        return f"// reward_stream network: {exc}"


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
                reward_timeline = gr.Code(
                    label="Episode reward timeline (GAIN / LOSS per step)",
                    language="markdown",
                    **_CODE_PANEL,
                )
                obs_context = gr.Code(
                    label="Observation context (logs · metrics · runbook · meta)",
                    language="json",
                    **_CODE_PANEL,
                )

                reset_btn.click(
                    run_reset,
                    [task_id, scenario_id, seed],
                    [session_id, observation_box, reset_status, reward_timeline, obs_context],
                )
                step_btn.click(
                    run_step,
                    [session_id, action_json],
                    [step_observation, step_meta, step_status, reward_timeline, obs_context],
                )
                state_btn.click(fetch_state, [session_id], [state_box, reward_timeline, obs_context])

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
                reward_stream_btn = gr.Button("Refresh server-wide reward stream")
                reward_stream_box = gr.Code(
                    label="Live reward stream (all sessions; from /metrics steps.reward_stream)",
                    language="markdown",
                    **_CODE_PANEL,
                )
                ws_info = gr.Textbox(
                    label="Live Stream Endpoint",
                    value=metrics_stream_info(),
                    lines=2,
                    max_lines=4,
                )
                metrics_btn.click(fetch_metrics, None, [metrics_box])
                reward_stream_btn.click(fetch_reward_stream, None, [reward_stream_box])
                gr.Timer(2.0).tick(fetch_metrics, None, [metrics_box])
                gr.Timer(2.0).tick(fetch_reward_stream, None, [reward_stream_box])

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
