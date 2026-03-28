from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx

from incident_ops_env.server.llm_provider import LLMProvider, get_provider


SEEDS = {1: 42, 2: 42, 3: 42}
logger = logging.getLogger("incident_ops_baseline")


def _extract_action_from_text(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None

    candidates: list[str] = [text]
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        candidates.append(fence_match.group(1).strip())

    decoder = json.JSONDecoder()
    for source in candidates:
        try:
            parsed = json.loads(source)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("action"), dict):
                    return parsed["action"]
                return parsed
        except json.JSONDecodeError:
            pass

        for idx, ch in enumerate(source):
            if ch != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(source[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                if isinstance(parsed.get("action"), dict):
                    return parsed["action"]
                return parsed
    return None


def _fallback_action_from_observation(observation: dict) -> dict:
    return ScriptedProvider()._choose_action(observation)


class ScriptedProvider(LLMProvider):
    """Deterministic fallback policy for reproducible baseline runs."""

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        if not messages:
            return json.dumps({"action_type": "no_op"})
        content = messages[-1].get("content", "")
        observation = self._extract_observation(content)
        action = self._choose_action(observation)
        return json.dumps(action)

    def _extract_observation(self, content: str) -> dict:
        marker = "Observation:\n"
        if marker in content:
            payload = content.split(marker, maxsplit=1)[1]
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {}
        return {}

    def _choose_action(self, observation: dict) -> dict:
        task_id = observation.get("task_id")
        step_number = int(observation.get("step_number", 0))

        if task_id == 1:
            alerts = observation.get("active_alerts", [])
            best = sorted(
                alerts,
                key=lambda a: (a.get("metadata", {}).get("resolved", False), a.get("severity", "P3")),
            )[0] if alerts else {}
            return {
                "action_type": "classify_alert",
                "severity": best.get("severity", "P2"),
                "service_name": best.get("service", "unknown"),
                "pattern_type": "database_overload",
            }

        if task_id == 2:
            primary_service = (observation.get("active_alerts") or [{}])[0].get("service", "checkout-service")
            if step_number == 0:
                return {"action_type": "filter_logs", "log_service": primary_service}
            if step_number == 1:
                return {"action_type": "get_metric", "metric_name": "error_rate", "service_name": primary_service}
            if step_number == 2:
                return {"action_type": "identify_service", "service_name": primary_service}
            return {
                "action_type": "propose_mitigation",
                "command": f"kubectl rollout undo deployment/{primary_service}",
            }

        if task_id == 3:
            runbook_steps = observation.get("runbook_steps", [])
            available = [s for s in runbook_steps if s.get("is_available") and not s.get("is_completed")]
            target = available[0] if available else None
            if not target:
                return {"action_type": "no_op"}
            if target.get("should_fail"):
                return {"action_type": "escalate", "escalation_team": "database", "escalation_reason": "step blocked"}
            if target.get("step_id") == "step_4" or observation.get("postmortem_prompt"):
                return {
                    "action_type": "write_postmortem",
                    "postmortem_text": (
                        "Database connection pool saturation impacted checkout-service. "
                        "We mitigated by runbook actions and added prevention for pool limits."
                    ),
                }
            return {
                "action_type": "execute_runbook_step",
                "runbook_step_id": target.get("step_id"),
                "command": target.get("correct_command", "noop"),
            }

        return {"action_type": "no_op"}


async def _try_step_with_retry(
    client: httpx.AsyncClient,
    prefix: str,
    session_id: str,
    action: dict,
    observation: dict,
    task_id: int,
    step: int,
    max_retries: int = 2,
) -> dict | None:
    """Attempt a step, retrying with progressively safer fallback actions on 422."""
    last_action = action
    for attempt in range(max_retries + 1):
        try:
            step_resp = await client.post(
                f"{prefix}/step",
                json={"action": last_action},
                headers={"X-Session-ID": session_id},
            )
            step_resp.raise_for_status()
            return step_resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            detail = ""
            try:
                detail = exc.response.json().get("detail", "")
            except Exception:
                detail = exc.response.text[:300]
            logger.warning(
                "Step 422 error task_id=%s step=%s attempt=%s action=%s detail=%s",
                task_id,
                step,
                attempt,
                last_action.get("action_type"),
                detail,
            )
            if attempt < max_retries:
                last_action = _get_safe_fallback_action(task_id, observation, attempt)
    return None


async def _drain_until_done(
    client: httpx.AsyncClient,
    prefix: str,
    session_id: str,
    observation: dict,
    task_id: int,
    done: bool,
    steps: int,
    max_steps_budget: int,
) -> tuple[dict, bool, int]:
    """If the main loop exited without ``done``, run scripted actions until terminal or budget.

    ``/grader`` returns 409 unless ``env.is_done``; LLM loops can exit early on ``max_steps`` or
    failed steps while the episode is still open.
    """
    if done:
        return observation, done, steps
    extra = 0
    cap = max(40, max_steps_budget, 25)
    obs = observation
    while not done and extra < cap:
        action = _fallback_action_from_observation(obs)
        step_data = await _try_step_with_retry(
            client, prefix, session_id, action, obs, task_id, steps + extra
        )
        if step_data is None:
            logger.warning(
                "Baseline drain: step failed task_id=%s extra=%s; stopping drain",
                task_id,
                extra,
            )
            break
        obs = step_data["observation"]
        done = step_data["done"]
        extra += 1
    return obs, done, steps + extra


def _get_safe_fallback_action(task_id: int, observation: dict, attempt: int) -> dict:
    """Return a guaranteed-valid action for each task when other actions fail."""
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
                "postmortem_text": "Incident mitigated via runbook execution. Root cause under investigation.",
            }
        if available:
            target = available[0]
            return {
                "action_type": "execute_runbook_step",
                "runbook_step_id": target.get("step_id", "step_1"),
                "command": target.get("correct_command", "echo noop"),
            }
        return {"action_type": "escalate", "escalation_team": "oncall", "escalation_reason": "automated fallback"}
    return {"action_type": "no_op"}


def _build_http_client() -> tuple[httpx.AsyncClient, str]:
    base_url = os.environ.get("BASELINE_ENV_URL", "http://localhost:7860")
    if base_url == "asgi://local":
        from incident_ops_env.server.app import app

        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://local", timeout=60.0), ""
    return httpx.AsyncClient(timeout=60.0), base_url


async def _run_task_with_client(task_id: int, seed: int, provider: LLMProvider, client: httpx.AsyncClient, prefix: str) -> dict:
    reset_resp = await client.post(f"{prefix}/reset", json={"task_id": task_id, "seed": seed})
    reset_resp.raise_for_status()
    reset_data = reset_resp.json()
    session_id = reset_data["session_id"]
    observation = reset_data["observation"]

    tasks_resp = await client.get(f"{prefix}/tasks")
    task_info = next(t for t in tasks_resp.json()["tasks"] if t["task_id"] == task_id)
    system_prompt = (
        "You are an expert SRE responding to a production incident. "
        "Respond with only valid JSON for the next action.\n"
        f"Task: {task_info['name']} - {task_info['description']}\n"
        f"Action schema: {json.dumps(task_info['action_schema'])}"
    )

    history: list[dict] = []
    done = False
    steps = 0
    invalid_output_count = 0
    max_steps = task_info.get("max_steps", 25)
    while not done and steps < max_steps:
        history.append({"role": "user", "content": f"Observation:\n{json.dumps(observation)}"})
        raw = provider.chat(history, system_prompt)
        action = _extract_action_from_text(raw)
        if not isinstance(action, dict) or not action.get("action_type"):
            invalid_output_count += 1
            excerpt = (raw or "").strip().replace("\n", " ")[:220]
            logger.warning(
                "Invalid model output task_id=%s step=%s using_fallback=true excerpt=%r",
                task_id,
                steps,
                excerpt,
            )
            action = _fallback_action_from_observation(observation)
        else:
            logger.debug(
                "Parsed model action task_id=%s step=%s action_type=%s",
                task_id,
                steps,
                action.get("action_type"),
            )
        history.append({"role": "assistant", "content": raw})

        step_data = await _try_step_with_retry(
            client, prefix, session_id, action, observation, task_id, steps
        )
        if step_data is None:
            invalid_output_count += 1
            logger.error(
                "Step failed after retry task_id=%s step=%s, forcing episode end",
                task_id,
                steps,
            )
            break
        observation = step_data["observation"]
        done = step_data["done"]
        steps += 1

    observation, done, steps = await _drain_until_done(
        client, prefix, session_id, observation, task_id, done, steps, max_steps
    )

    if not done:
        scenario_id = "unknown"
        try:
            st_resp = await client.get(f"{prefix}/state", headers={"X-Session-ID": session_id})
            if st_resp.is_success:
                scenario_id = st_resp.json().get("scenario_id", "unknown")
        except Exception:
            pass
        logger.error(
            "Baseline task_id=%s episode still open after drain (steps=%s); skipping grader",
            task_id,
            steps,
        )
        return {
            "task_id": task_id,
            "score": 0.0,
            "steps_used": steps,
            "scenario_id": scenario_id,
            "seed": seed,
            "invalid_output_count": invalid_output_count,
            "grader_skipped": True,
            "detail": "Episode did not reach terminal state; /grader requires is_done.",
        }

    grader_resp = await client.post(f"{prefix}/grader", json={"session_id": session_id})
    grader_resp.raise_for_status()
    grader_data = grader_resp.json()
    return {
        "task_id": task_id,
        "score": grader_data["score"],
        "steps_used": steps,
        "scenario_id": grader_data.get("scenario_id", "unknown"),
        "seed": seed,
        "invalid_output_count": invalid_output_count,
    }


async def run_task(
    task_id: int, seed: int, provider: LLMProvider, *, use_asgi_local: bool = False
) -> dict:
    """Run one task episode. Use ``use_asgi_local=True`` when calling from inside the server so
    reset/step/grader hit the same in-memory SessionManager (required when uvicorn uses workers>1).
    """
    if use_asgi_local:
        client, prefix = _build_http_client_asgi_local()
        async with client:
            return await _run_task_with_client(task_id, seed, provider, client, prefix)

    client, prefix = _build_http_client()
    async with client:
        try:
            return await _run_task_with_client(task_id, seed, provider, client, prefix)
        except httpx.ConnectError:
            if os.environ.get("BASELINE_ENV_URL", "http://localhost:7860") == "asgi://local":
                raise
            logger.warning(
                "Could not connect to BASELINE_ENV_URL; retrying task_id=%s with in-process asgi://local",
                task_id,
            )
    fallback_client, fallback_prefix = _build_http_client_asgi_local()
    async with fallback_client:
        return await _run_task_with_client(task_id, seed, provider, fallback_client, fallback_prefix)


def _build_http_client_asgi_local() -> tuple[httpx.AsyncClient, str]:
    from incident_ops_env.server.app import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://local", timeout=60.0), ""


async def run_baseline(*, use_asgi_local: bool = False) -> dict:
    provider_name_hint = os.environ.get("BASELINE_PROVIDER", "").strip().lower()
    if provider_name_hint == "scripted":
        provider: LLMProvider = ScriptedProvider()
    else:
        provider = get_provider()
    provider_name = getattr(provider, "model", None) or getattr(provider, "model_name", None)
    provider_name = provider_name or type(provider).__name__.replace("Provider", "").lower()
    scores = {}
    invalid_outputs_total = 0
    for task_id, seed in SEEDS.items():
        task_result = await run_task(task_id, seed, provider, use_asgi_local=use_asgi_local)
        invalid_outputs_total += int(task_result.get("invalid_output_count", 0))
        scores[f"task_{task_id}"] = {
            "score": task_result["score"],
            "seed": task_result["seed"],
            "scenario_id": task_result["scenario_id"],
            "invalid_output_count": task_result["invalid_output_count"],
        }
    average = round(sum(x["score"] for x in scores.values()) / len(scores), 2)
    return {
        "baseline_model": provider_name,
        "scores": scores,
        "average_score": average,
        "invalid_output_count_total": invalid_outputs_total,
    }


def run_baseline_sync(*, use_asgi_local: bool = False) -> dict:
    return asyncio.run(run_baseline(use_asgi_local=use_asgi_local))


if __name__ == "__main__":
    print(json.dumps(run_baseline_sync(), indent=2))
