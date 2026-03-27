from __future__ import annotations

import json
import os
from typing import Any

import gradio as gr
import httpx


def _backend_base_url() -> str:
    return os.environ.get("UI_BACKEND_URL", "http://127.0.0.1:7860")


def _request(method: str, path: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict:
    url = _backend_base_url().rstrip("/") + path
    with httpx.Client(timeout=60.0) as client:
        response = client.request(method=method, url=url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


def run_reset(task_id: int, scenario_id: str, seed: int | None) -> tuple[str, str, str]:
    body: dict[str, Any] = {"task_id": int(task_id)}
    if scenario_id.strip():
        body["scenario_id"] = scenario_id.strip()
    if seed is not None:
        body["seed"] = int(seed)
    payload = _request("POST", "/reset", body)
    return (
        payload["session_id"],
        json.dumps(payload["observation"], indent=2),
        json.dumps({"status": "reset_complete"}, indent=2),
    )


def run_step(session_id: str, action_json: str) -> tuple[str, str, str]:
    if not session_id.strip():
        return "", "", "Missing session_id. Run reset first."
    try:
        action_payload = json.loads(action_json)
    except json.JSONDecodeError as exc:
        return "", "", f"Invalid action JSON: {exc}"
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


def fetch_state(session_id: str) -> str:
    if not session_id.strip():
        return "Missing session_id."
    payload = _request("GET", "/state", headers={"X-Session-ID": session_id.strip()})
    return json.dumps(payload, indent=2)


def fetch_tasks() -> str:
    payload = _request("GET", "/tasks")
    return json.dumps(payload, indent=2)


def fetch_metrics() -> str:
    payload = _request("GET", "/metrics")
    return json.dumps(payload, indent=2)


def run_baseline() -> str:
    payload = _request("POST", "/baseline")
    return json.dumps(payload, indent=2)


def run_grader(session_id: str) -> str:
    if not session_id.strip():
        return "Missing session_id."
    try:
        payload = _request("POST", "/grader", {"session_id": session_id.strip()})
        return json.dumps(payload, indent=2)
    except httpx.HTTPStatusError as exc:
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
    with open(file_obj.name, encoding="utf-8") as handle:
        content = json.load(handle)
    payload = _request("POST", "/scenarios/upload", {"scenario": content})
    return json.dumps(payload, indent=2)


def validate_scenario(file_obj) -> str:
    if file_obj is None:
        return "No file selected."
    with open(file_obj.name, encoding="utf-8") as handle:
        content = json.load(handle)
    payload = _request("POST", "/scenarios/validate", {"scenario": content})
    return json.dumps(payload, indent=2)


def list_scenarios() -> str:
    payload = _request("GET", "/scenarios")
    return json.dumps(payload, indent=2)


def metrics_stream_info() -> str:
    base = _backend_base_url().rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws/metrics"
    return f"Live metrics stream endpoint: {ws_url}"


def build_gradio_app() -> gr.Blocks:
    with gr.Blocks(title="IncidentOpsEnv Observability UI") as demo:
        gr.Markdown("# IncidentOpsEnv - Gradio Observability")
        gr.Markdown("Use this UI to inspect episodes, backend metrics, graders, baseline runs, and scenario uploads.")

        with gr.Tab("Episode Runner"):
            with gr.Row():
                task_id = gr.Dropdown(choices=[1, 2, 3], value=1, label="Task ID")
                scenario_id = gr.Textbox(label="Scenario ID (optional)")
                seed = gr.Number(label="Seed (optional)", precision=0)
            reset_btn = gr.Button("Reset Episode")
            session_id = gr.Textbox(label="Session ID")
            observation_box = gr.Code(label="Observation", language="json")
            reset_status = gr.Textbox(label="Reset Status")

            gr.Markdown("### Step with JSON action")
            action_json = gr.Code(
                value='{\n  "action_type": "no_op"\n}',
                language="json",
                label="Action JSON",
            )
            step_btn = gr.Button("Run Step")
            step_observation = gr.Code(label="Observation After Step", language="json")
            step_meta = gr.Code(label="Reward/Done/Info", language="json")
            step_status = gr.Textbox(label="Step Status")
            state_btn = gr.Button("Fetch State")
            state_box = gr.Code(label="Current State", language="json")

            reset_btn.click(run_reset, [task_id, scenario_id, seed], [session_id, observation_box, reset_status])
            step_btn.click(run_step, [session_id, action_json], [step_observation, step_meta, step_status])
            state_btn.click(fetch_state, [session_id], [state_box])

        with gr.Tab("Tasks and Grader"):
            tasks_btn = gr.Button("Load /tasks")
            tasks_box = gr.Code(label="Tasks", language="json")
            grader_btn = gr.Button("Run /grader for Session")
            grader_box = gr.Code(label="Grader Result", language="json")
            tasks_btn.click(fetch_tasks, None, [tasks_box])
            grader_btn.click(run_grader, [session_id], [grader_box])

        with gr.Tab("Metrics"):
            metrics_btn = gr.Button("Refresh Metrics")
            metrics_box = gr.Code(label="/metrics snapshot", language="json")
            ws_info = gr.Textbox(label="Live Stream Endpoint", value=metrics_stream_info())
            metrics_btn.click(fetch_metrics, None, [metrics_box])
            gr.Timer(2.0).tick(fetch_metrics, None, [metrics_box])

        with gr.Tab("Baseline"):
            baseline_btn = gr.Button("Run /baseline")
            baseline_box = gr.Code(label="Baseline Result", language="json")
            baseline_btn.click(run_baseline, None, [baseline_box])

        with gr.Tab("Scenario Upload/Validation"):
            file_input = gr.File(label="Upload scenario JSON")
            validate_btn = gr.Button("Validate Scenario")
            validate_out = gr.Code(label="Validation Result", language="json")
            upload_btn = gr.Button("Upload Scenario")
            upload_out = gr.Code(label="Upload Result", language="json")
            list_btn = gr.Button("List Scenarios")
            list_out = gr.Code(label="Scenario Registry", language="json")
            validate_btn.click(validate_scenario, [file_input], [validate_out])
            upload_btn.click(upload_scenario, [file_input], [upload_out])
            list_btn.click(list_scenarios, None, [list_out])
    return demo
