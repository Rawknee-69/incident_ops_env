from __future__ import annotations

import asyncio
import json
import os

import httpx

from incident_ops_env.server.llm_provider import LLMProvider, get_provider


SEEDS = {1: 42, 2: 42, 3: 42}


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


def _build_http_client() -> tuple[httpx.AsyncClient, str]:
    base_url = os.environ.get("BASELINE_ENV_URL", "http://localhost:7860")
    if base_url == "asgi://local":
        from incident_ops_env.server.app import app

        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://local", timeout=60.0), ""
    return httpx.AsyncClient(timeout=60.0), base_url


async def run_task(task_id: int, seed: int, provider: LLMProvider) -> dict:
    client, prefix = _build_http_client()
    async with client:
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
        while not done:
            history.append({"role": "user", "content": f"Observation:\n{json.dumps(observation)}"})
            raw = provider.chat(history, system_prompt)
            try:
                action = json.loads(raw.strip())
            except json.JSONDecodeError:
                action = {"action_type": "no_op"}
            history.append({"role": "assistant", "content": raw})
            step_resp = await client.post(
                f"{prefix}/step",
                json={"action": action},
                headers={"X-Session-ID": session_id},
            )
            step_resp.raise_for_status()
            step_data = step_resp.json()
            observation = step_data["observation"]
            done = step_data["done"]
            steps += 1

        grader_resp = await client.post(f"{prefix}/grader", json={"session_id": session_id})
        grader_resp.raise_for_status()
        grader_data = grader_resp.json()
        return {
            "task_id": task_id,
            "score": grader_data["score"],
            "steps_used": steps,
            "scenario_id": grader_data.get("scenario_id", "unknown"),
            "seed": seed,
        }


async def run_baseline() -> dict:
    provider_name_hint = os.environ.get("BASELINE_PROVIDER", "").strip().lower()
    if provider_name_hint == "scripted":
        provider: LLMProvider = ScriptedProvider()
    else:
        provider = get_provider()
    provider_name = getattr(provider, "model", None) or getattr(provider, "model_name", None)
    provider_name = provider_name or type(provider).__name__.replace("Provider", "").lower()
    scores = {}
    for task_id, seed in SEEDS.items():
        task_result = await run_task(task_id, seed, provider)
        scores[f"task_{task_id}"] = {
            "score": task_result["score"],
            "seed": task_result["seed"],
            "scenario_id": task_result["scenario_id"],
        }
    average = round(sum(x["score"] for x in scores.values()) / len(scores), 2)
    return {"baseline_model": provider_name, "scores": scores, "average_score": average}


def run_baseline_sync() -> dict:
    return asyncio.run(run_baseline())


if __name__ == "__main__":
    print(json.dumps(run_baseline_sync(), indent=2))
