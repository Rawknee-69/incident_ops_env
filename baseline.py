from __future__ import annotations

import asyncio
import json
import os

import httpx

from incident_ops_env.server.llm_provider import LLMProvider, get_provider


BASE_URL = os.environ.get("BASELINE_ENV_URL", "http://localhost:7860")
SEEDS = {1: 42, 2: 42, 3: 42}


async def run_task(task_id: int, seed: int, provider: LLMProvider) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        reset_resp = await client.post(f"{BASE_URL}/reset", json={"task_id": task_id, "seed": seed})
        reset_resp.raise_for_status()
        reset_data = reset_resp.json()
        session_id = reset_data["session_id"]
        observation = reset_data["observation"]

        tasks_resp = await client.get(f"{BASE_URL}/tasks")
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
                f"{BASE_URL}/step",
                json={"action": action},
                headers={"X-Session-ID": session_id},
            )
            step_resp.raise_for_status()
            step_data = step_resp.json()
            observation = step_data["observation"]
            done = step_data["done"]
            steps += 1

        grader_resp = await client.post(f"{BASE_URL}/grader", json={"session_id": session_id})
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
