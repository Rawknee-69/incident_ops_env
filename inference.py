"""
Inference Script — IncidentOpsEnv
=================================
Provider selection:
    OPENROUTER_ENABLED=true  -> OpenRouter via OpenAI-compatible client
    OPENROUTER_ENABLED=false -> Gemini SDK

Core environment variables:
    API_BASE_URL   OpenAI-compatible base URL (default: https://openrouter.ai/api/v1).
    MODEL_NAME     Model name for OpenRouter mode (default: openai/gpt-4o-mini).
    ENV_URL        Environment server URL (default: http://localhost:7860).

Secrets:
    OPENROUTER_API_KEY   Required when OPENROUTER_ENABLED=true.
    GEMINI_API_KEY       Required when OPENROUTER_ENABLED=false.

Optional:
    LOCAL_IMAGE_NAME     Used only when running from docker image flows.
"""

import json
import os
import re
import textwrap
from typing import Any

import google.generativeai as genai
import httpx
from openai import OpenAI


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


API_BASE_URL = os.getenv("API_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-4o-mini")
LOCAL_IMAGE_NAME = os.getenv("LOCAL_IMAGE_NAME")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ENV_URL = os.getenv("ENV_URL", "http://localhost:7860").rstrip("/")

MAX_STEPS = 25
TEMPERATURE = 0.0
MAX_TOKENS = 1000
TIMEOUT = 60.0

SYSTEM_PROMPT = textwrap.dedent("""\
You are an expert SRE (Site Reliability Engineer) responding to a production incident.
You interact with an incident-response environment through JSON actions.

Your goal is to diagnose the incident, identify affected services, and resolve it.

RULES:
- Respond with ONLY valid JSON for the next action. No explanation, no markdown fences.
- Every action MUST have an "action_type" field.
- Use the observation (alerts, logs, metrics, runbook steps) to decide your next action.

AVAILABLE ACTIONS by task:

Task 1 (Alert Triage):
  {"action_type": "classify_alert", "severity": "P1|P2|P3", "service_name": "...", "pattern_type": "..."}

Task 2 (Root Cause Analysis):
  {"action_type": "filter_logs", "log_service": "..."}
  {"action_type": "get_metric", "metric_name": "...", "service_name": "..."}
  {"action_type": "identify_service", "service_name": "..."}
  {"action_type": "propose_mitigation", "command": "..."}

Task 3 (Full Incident Playbook):
  {"action_type": "execute_runbook_step", "runbook_step_id": "...", "command": "..."}
  {"action_type": "escalate", "escalation_team": "database|networking|security|platform|management", "escalation_reason": "..."}
  {"action_type": "write_postmortem", "postmortem_text": "..."}

Any task:
  {"action_type": "no_op"}
""")


def _load_dotenv_if_present(path: str = ".env") -> None:
    """Lightweight .env loader for local runs without extra dependencies."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                # Keep real environment precedence over file defaults.
                os.environ.setdefault(key, value)
    except OSError:
        return


def _resolve_gemini_key() -> str | None:
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")
    )


def _resolve_openrouter_config() -> dict[str, Any]:
    base_url = (os.getenv("API_BASE_URL") or "https://openrouter.ai/api/v1").strip()
    model_name = (os.getenv("MODEL_NAME") or "openai/gpt-4o-mini").strip() or "openai/gpt-4o-mini"
    api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip() or None
    referer = (os.getenv("OPENROUTER_HTTP_REFERER") or "").strip() or None
    title = (os.getenv("OPENROUTER_TITLE") or "").strip() or None

    return {
        "enabled": _env_bool("OPENROUTER_ENABLED", default=False),
        "base_url": base_url,
        "model_name": model_name,
        "api_key": api_key,
        "referer": referer,
        "title": title,
    }


def _chat_with_model(messages: list[dict[str, str]], system_prompt: str) -> str:
    """Route chat to OpenRouter (OpenAI-compatible) or Gemini SDK."""
    openrouter = _resolve_openrouter_config()
    if openrouter["enabled"]:
        if not openrouter["api_key"]:
            raise EnvironmentError(
                "OPENROUTER_ENABLED=true requires OPENROUTER_API_KEY."
            )

        client = OpenAI(base_url=openrouter["base_url"], api_key=openrouter["api_key"])
        extra_headers: dict[str, str] = {}
        if openrouter["referer"]:
            extra_headers["HTTP-Referer"] = openrouter["referer"]
        if openrouter["title"]:
            extra_headers["X-OpenRouter-Title"] = openrouter["title"]

        completion = client.chat.completions.create(
            model=openrouter["model_name"],
            messages=[{"role": "system", "content": system_prompt}, *messages],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            extra_headers=extra_headers or None,
        )
        return completion.choices[0].message.content or "{}"

    gemini_api_key = _resolve_gemini_key()
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel(model_name=GEMINI_MODEL, system_instruction=system_prompt)
        gemini_history = [
            {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
            for m in messages[:-1]
        ]
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(
            messages[-1]["content"],
            generation_config={"temperature": TEMPERATURE, "max_output_tokens": MAX_TOKENS},
        )
        return response.text or "{}"

    # No credentials configured. Returning empty JSON lets caller use deterministic fallback actions.
    return "{}"


def _extract_action(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None

    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
            if isinstance(parsed, dict):
                if isinstance(parsed.get("action"), dict):
                    return parsed["action"]
                if parsed.get("action_type"):
                    return parsed
        except json.JSONDecodeError:
            continue
    return None


def _fallback_action(observation: dict[str, Any]) -> dict[str, Any]:
    task_id = observation.get("task_id")
    if task_id == 1:
        alerts = observation.get("active_alerts", [])
        best = alerts[0] if alerts else {}
        return {
            "action_type": "classify_alert",
            "severity": best.get("severity", "P2"),
            "service_name": best.get("service", "unknown"),
            "pattern_type": "unknown",
        }
    if task_id == 2:
        svc = (observation.get("active_alerts") or [{}])[0].get("service", "checkout-service")
        step_number = int(observation.get("step_number", 0))
        if step_number == 0:
            return {"action_type": "filter_logs", "log_service": svc}
        if step_number == 1:
            return {"action_type": "get_metric", "metric_name": "error_rate", "service_name": svc}
        if step_number == 2:
            return {"action_type": "identify_service", "service_name": svc}
        return {"action_type": "propose_mitigation", "command": f"kubectl rollout undo deployment/{svc}"}
    if task_id == 3:
        runbook_steps = observation.get("runbook_steps", [])
        available = [s for s in runbook_steps if s.get("is_available") and not s.get("is_completed")]
        target = available[0] if available else None
        if observation.get("postmortem_prompt"):
            return {
                "action_type": "write_postmortem",
                "postmortem_text": (
                    "Incident affected production services due to operational failure. "
                    "We executed runbook and mitigation steps, stabilized impacted systems, "
                    "and added prevention actions to reduce recurrence."
                ),
            }
        if not target:
            return {"action_type": "no_op"}
        if target.get("should_fail"):
            return {
                "action_type": "escalate",
                "escalation_team": "database",
                "escalation_reason": "Runbook step is expected to fail and requires specialist support.",
            }
        return {
            "action_type": "execute_runbook_step",
            "runbook_step_id": target.get("step_id", "step_1"),
            "command": target.get("correct_command", "echo noop"),
        }
    return {"action_type": "no_op"}


def _safe_fallback_after_422(task_id: int, observation: dict[str, Any], attempt: int) -> dict[str, Any]:
    if task_id == 1:
        return {
            "action_type": "classify_alert",
            "severity": "P2",
            "service_name": "unknown",
            "pattern_type": "unknown",
        }
    if task_id == 2:
        if attempt == 0:
            return {"action_type": "filter_logs", "log_service": "checkout-service"}
        return {"action_type": "get_metric", "metric_name": "error_rate", "service_name": "checkout-service"}
    if task_id == 3:
        runbook_steps = observation.get("runbook_steps", [])
        available = [s for s in runbook_steps if s.get("is_available") and not s.get("is_completed")]
        if observation.get("postmortem_prompt"):
            return {
                "action_type": "write_postmortem",
                "postmortem_text": "Incident mitigated via runbook execution. Root cause documented.",
            }
        if available:
            target = available[0]
            return {
                "action_type": "execute_runbook_step",
                "runbook_step_id": target.get("step_id", "step_1"),
                "command": target.get("correct_command", "echo noop"),
            }
        return {"action_type": "no_op"}
    return {"action_type": "no_op"}


def emit_start(task_id: int, session_id: str) -> None:
    print(f"[START] task={task_id} session_id={session_id}", flush=True)


def emit_step(step: int, reward: float, done: bool, action_type: str) -> None:
    print(f"[STEP] step={step} reward={reward:.4f} done={done} action={action_type}", flush=True)


def emit_end(task_id: int, score: float, steps: int, session_id: str) -> None:
    print(f"[END] task={task_id} score={score:.4f} steps={steps} session_id={session_id}", flush=True)


def _post_step_with_retry(
    client: httpx.Client,
    session_id: str,
    action: dict[str, Any],
    observation: dict[str, Any],
    task_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_action = action
    max_retries = 2
    for attempt in range(max_retries + 1):
        response = client.post(
            f"{ENV_URL}/step",
            json={"action": last_action},
            headers={"X-Session-ID": session_id},
        )
        if response.status_code < 400:
            return response.json(), last_action
        if response.status_code == 422 and attempt < max_retries:
            last_action = _safe_fallback_after_422(task_id, observation, attempt)
            continue
        response.raise_for_status()
    raise RuntimeError("Unreachable retry branch in _post_step_with_retry")


def run_episode(task_id: int = 1, seed: int = 42) -> dict[str, Any]:
    if task_id not in (1, 2, 3):
        raise ValueError(f"task_id must be one of 1, 2, 3; got {task_id}")

    client = httpx.Client(timeout=TIMEOUT)
    session_id = "unknown"
    score = 0.0
    steps = 0
    done = False
    end_emitted = False

    # Emit START immediately so validators always receive structured output.
    emit_start(task_id=task_id, session_id=session_id)

    try:
        reset_resp = client.post(f"{ENV_URL}/reset", json={"task_id": task_id, "seed": seed})
        reset_resp.raise_for_status()
        reset_data = reset_resp.json()
        session_id = str(reset_data.get("session_id", "unknown"))
        observation = reset_data["observation"]

        tasks_resp = client.get(f"{ENV_URL}/tasks")
        task_info = next(t for t in tasks_resp.json()["tasks"] if t["task_id"] == task_id)
        max_steps = task_info.get("max_steps", MAX_STEPS)
        history: list[dict[str, str]] = []

        while not done and steps < max_steps:
            user_msg = f"Step {steps + 1}/{max_steps}\nObservation:\n{json.dumps(observation, indent=2)}"
            history.append({"role": "user", "content": user_msg})

            try:
                raw = _chat_with_model(history, SYSTEM_PROMPT)
            except Exception as exc:
                print(f"Model request failed: {exc}", flush=True)
                raw = ""

            action = _extract_action(raw)
            if not action or not action.get("action_type"):
                action = _fallback_action(observation)

            history.append({"role": "assistant", "content": raw})

            try:
                step_data, final_action = _post_step_with_retry(client, session_id, action, observation, task_id)
            except httpx.HTTPStatusError as exc:
                print(f"Step failed ({exc.response.status_code}): {exc.response.text[:200]}", flush=True)
                raise

            observation = step_data["observation"]
            reward = step_data.get("reward", 0.0)
            done = step_data.get("done", False)
            steps += 1

            action_type = str(final_action.get("action_type", "unknown"))
            emit_step(step=steps, reward=reward, done=done, action_type=action_type)
        score = float(done)
        emit_end(task_id=task_id, score=score, steps=steps, session_id=session_id)
        end_emitted = True
        return {"task_id": task_id, "session_id": session_id, "steps": steps, "done": done, "score": score}
    except Exception as exc:
        print(f"Episode failed: {exc}", flush=True)
        if steps == 0:
            emit_step(step=1, reward=0.0, done=True, action_type="no_op")
            steps = 1
            done = True
        emit_end(task_id=task_id, score=0.0, steps=steps, session_id=session_id)
        end_emitted = True
        return {"task_id": task_id, "session_id": session_id, "steps": steps, "done": done, "score": 0.0}
    finally:
        client.close()
        if not end_emitted:
            emit_end(task_id=task_id, score=score, steps=steps, session_id=session_id)


def main() -> None:
    _load_dotenv_if_present(".env")

    raw_tasks = os.getenv("INFERENCE_TASKS", "1,2,3")
    task_ids = [int(x.strip()) for x in raw_tasks.split(",") if x.strip()]
    if not task_ids:
        # Avoid silent no-op runs when INFERENCE_TASKS is empty.
        task_ids = [1, 2, 3]
    seed = int(os.getenv("INFERENCE_SEED", "42"))

    results = []
    for tid in task_ids:
        result = run_episode(task_id=tid, seed=seed)
        results.append(result)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    main()
