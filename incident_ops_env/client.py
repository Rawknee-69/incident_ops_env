from __future__ import annotations

from openenv.core.http_env_client import HTTPEnvClient

from incident_ops_env.models import IncidentAction, IncidentObservation, IncidentReward, IncidentState, IncidentStepResult


class IncidentOpsEnv(HTTPEnvClient[IncidentAction, IncidentObservation]):
    def _step_payload(self, action: IncidentAction) -> dict:
        return {"action": action.model_dump(exclude_none=True)}

    def _parse_result(self, payload: dict) -> IncidentStepResult:
        reward_model_payload = payload.get("reward_model")
        if reward_model_payload is None:
            reward_model_payload = {
                "value": payload["reward"],
                "breakdown": payload.get("info", {}).get("reward_breakdown", {}),
            }
        return IncidentStepResult(
            observation=IncidentObservation(**payload["observation"]),
            reward=payload["reward"],
            reward_model=IncidentReward(**reward_model_payload),
            done=payload["done"],
            info=payload.get("info", {}),
        )

    def _parse_state(self, payload: dict) -> IncidentState:
        return IncidentState(**payload)

    async def reset(self, task_id: int = 1, scenario_id: str | None = None, seed: int | None = None):
        body = {"task_id": task_id}
        if scenario_id:
            body["scenario_id"] = scenario_id
        if seed is not None:
            body["seed"] = seed
        return await self._reset(body)
