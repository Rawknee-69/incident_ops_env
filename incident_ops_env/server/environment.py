from __future__ import annotations

import random
import uuid
from copy import deepcopy
from typing import Any

from incident_ops_env.models import (
    ActionType,
    IncidentAction,
    IncidentObservation,
    IncidentState,
    IncidentStepResult,
    MetricSnapshot,
    RunbookStep,
)
from incident_ops_env.server.graders import grade_task1, grade_task2, grade_task3
from incident_ops_env.server.reward import compute_step_reward
from incident_ops_env.server.scenario_loader import list_scenarios_for_task, load_scenario_by_id


MAX_STEPS_BY_TASK = {1: 5, 2: 15, 3: 25}


class IncidentOpsEnvironment:
    def __init__(self) -> None:
        self.episode_id = ""
        self.task_id = 1
        self.scenario_id = ""
        self.max_steps = MAX_STEPS_BY_TASK[1]
        self.step_number = 0
        self.total_reward_so_far = 0.0
        self.is_done = False
        self.done_reason: str | None = None
        self.reward_breakdown: dict[str, float] = {}
        self.current_scenario: dict[str, Any] = {}
        self.recent_logs: list[dict] = []
        self.current_metrics: list[dict] = []
        self.runbook_steps: list[dict] = []
        self.time_elapsed_seconds = 0.0
        self.last_action_result: str | None = None
        self.last_action_was_valid = True
        self.failed_step_ids: set[str] = set()
        self.irrelevant_log_queries = 0
        self.action_history: list[dict] = []
        self.step_rewards: list[float] = []

    def reset(self, task_id: int, scenario_id: str | None = None, seed: int | None = None) -> IncidentObservation:
        if task_id not in (1, 2, 3):
            raise ValueError("task_id must be 1, 2, or 3")

        if seed is not None:
            random.seed(seed)

        scenario = load_scenario_by_id(scenario_id) if scenario_id else random.choice(list_scenarios_for_task(task_id))
        self.current_scenario = deepcopy(scenario)
        self.episode_id = str(uuid.uuid4())
        self.task_id = task_id
        self.scenario_id = scenario["scenario_id"]
        self.max_steps = MAX_STEPS_BY_TASK[task_id]
        self.step_number = 0
        self.total_reward_so_far = 0.0
        self.is_done = False
        self.done_reason = None
        self.reward_breakdown = {}
        self.recent_logs = []
        self.current_metrics = []
        self.runbook_steps = deepcopy(scenario.get("runbook", []))
        self.time_elapsed_seconds = 0.0
        self.last_action_result = None
        self.last_action_was_valid = True
        self.failed_step_ids = set()
        self.irrelevant_log_queries = 0
        self.action_history = []
        self.step_rewards = []
        return self._build_observation()

    def step(self, action: IncidentAction) -> IncidentStepResult:
        if self.is_done:
            raise RuntimeError("Episode is already complete. Call reset() to start a new one.")

        valid, reason = self._validate_action(action)
        action_result: dict[str, Any] = {}

        if not valid:
            self.last_action_was_valid = False
            self.last_action_result = f"Invalid action: {reason}"
            action_result["invalid_action"] = True
        else:
            self.last_action_was_valid = True
            action_result = self._execute_action(action)
            self.step_number += 1
            self.time_elapsed_seconds += 30.0

        if self.step_number >= self.max_steps and not self.is_done:
            self.is_done = True
            self.done_reason = "max_steps_reached"

        if action_result.get("task_complete"):
            self.is_done = True
            self.done_reason = "task_complete"

        action_result["episode_completed"] = self.is_done
        reward, breakdown = compute_step_reward(
            action=action,
            action_result=action_result,
            episode_state={
                "step_number": self.step_number,
                "max_steps": self.max_steps,
                "irrelevant_log_queries": self.irrelevant_log_queries,
            },
            scenario_ground_truth=self.current_scenario.get("ground_truth", {}),
        )
        self.total_reward_so_far += reward
        self.step_rewards.append(reward)
        for key, value in breakdown.items():
            if key == "total":
                continue
            self.reward_breakdown[key] = self.reward_breakdown.get(key, 0.0) + value

        history_item = action.model_dump(exclude_none=True)
        history_item["_was_successful"] = bool(action_result.get("step_success", False))
        history_item["_step_number"] = self.step_number
        history_item["_reward"] = reward
        self.action_history.append(history_item)

        return IncidentStepResult(
            observation=self._build_observation(),
            reward=reward,
            done=self.is_done,
            info={
                "episode_id": self.episode_id,
                "step_number": self.step_number,
                "reward_breakdown": breakdown,
                "done_reason": self.done_reason,
            },
        )

    def state(self) -> IncidentState:
        return IncidentState(
            episode_id=self.episode_id,
            task_id=self.task_id,
            scenario_id=self.scenario_id,
            step_number=self.step_number,
            max_steps=self.max_steps,
            total_reward_so_far=self.total_reward_so_far,
            is_done=self.is_done,
            done_reason=self.done_reason,
            reward_breakdown=self.reward_breakdown,
        )

    def grade(self) -> float:
        ground_truth = self.current_scenario.get("ground_truth", {})
        if self.task_id == 1:
            return grade_task1(self.action_history, ground_truth)
        if self.task_id == 2:
            return grade_task2(self.action_history, ground_truth)
        return grade_task3(self.action_history, ground_truth)

    def _validate_action(self, action: IncidentAction) -> tuple[bool, str]:
        task_legal_actions = {
            1: {ActionType.CLASSIFY_ALERT, ActionType.NO_OP},
            2: {
                ActionType.FILTER_LOGS,
                ActionType.GET_METRIC,
                ActionType.IDENTIFY_SERVICE,
                ActionType.PROPOSE_MITIGATION,
                ActionType.CLASSIFY_ALERT,
                ActionType.NO_OP,
            },
            3: set(ActionType),
        }
        if action.action_type not in task_legal_actions[self.task_id]:
            return False, f"Action {action.action_type.value} is not legal for task {self.task_id}."
        if action.action_type == ActionType.CLASSIFY_ALERT:
            if not (action.severity and action.service_name and action.pattern_type):
                return False, "classify_alert requires severity, service_name, pattern_type."
        if action.action_type == ActionType.FILTER_LOGS and not action.log_service:
            return False, "filter_logs requires log_service."
        if action.action_type == ActionType.GET_METRIC and not action.metric_name:
            return False, "get_metric requires metric_name."
        if action.action_type == ActionType.PROPOSE_MITIGATION and not action.command:
            return False, "propose_mitigation requires command."
        if action.action_type == ActionType.WRITE_POSTMORTEM and not (action.postmortem_text or "").strip():
            return False, "write_postmortem requires non-empty postmortem_text."
        if action.action_type == ActionType.EXECUTE_RUNBOOK_STEP and not (action.runbook_step_id and action.command):
            return False, "execute_runbook_step requires runbook_step_id and command."
        if action.action_type == ActionType.ESCALATE and not action.escalation_team:
            return False, "escalate requires escalation_team."
        return True, ""

    def _execute_action(self, action: IncidentAction) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if action.action_type == ActionType.NO_OP:
            self.last_action_result = "No action taken."
            return result

        if action.action_type == ActionType.FILTER_LOGS:
            logs = self.current_scenario.get("log_database", {}).get(action.log_service or "", [])
            filtered = logs
            if action.log_level:
                filtered = [entry for entry in filtered if entry.get("level") == action.log_level]
            if action.log_keyword:
                filtered = [entry for entry in filtered if action.log_keyword.lower() in entry.get("message", "").lower()]
            self.recent_logs = filtered[:20]
            if action.log_service != self.current_scenario.get("ground_truth", {}).get("root_cause_service"):
                self.irrelevant_log_queries += 1
            self.last_action_result = f"Logs filtered. {len(self.recent_logs)} lines returned."
            return result

        if action.action_type == ActionType.GET_METRIC:
            service_name = action.service_name or self.current_scenario.get("ground_truth", {}).get("root_cause_service")
            history = (
                self.current_scenario.get("metric_database", {})
                .get(service_name, {})
                .get(action.metric_name or "", [])
            )
            window = action.metric_window_minutes or 60
            self.current_metrics = history[-window:]
            self.last_action_result = f"Metric query returned {len(self.current_metrics)} snapshots."
            return result

        if action.action_type == ActionType.EXECUTE_RUNBOOK_STEP:
            target = next(
                (step for step in self.runbook_steps if step.get("step_id") == action.runbook_step_id),
                None,
            )
            if target is None:
                self.last_action_result = "Runbook step not found."
                return result
            if not target.get("is_available"):
                self.last_action_result = "Runbook step not available yet."
                return result
            if target.get("should_fail"):
                self.failed_step_ids.add(target["step_id"])
                if target["step_id"] in self.failed_step_ids and target.get("is_completed"):
                    result["retried_failed_step"] = True
                self.last_action_result = "Step failed: simulated failure. Consider escalating to the database team."
                return result
            if action.command == target.get("correct_command"):
                target["is_completed"] = True
                result["step_success"] = True
                self._unlock_next_step(target["step_id"])
                self._apply_dynamic_updates(target["step_id"])
                self.last_action_result = f"Runbook step {target['step_id']} completed."
            else:
                self.last_action_result = "Incorrect command for runbook step."
            return result

        if action.action_type == ActionType.ESCALATE:
            result["task_complete"] = True
            self.last_action_result = f"Escalated to {action.escalation_team}."
            return result

        if action.action_type == ActionType.WRITE_POSTMORTEM:
            required = [
                kw.lower()
                for kw in self.current_scenario.get("ground_truth", {}).get("required_postmortem_keywords", [])
            ]
            text = (action.postmortem_text or "").lower()
            matches = sum(1 for kw in required if kw in text)
            result["postmortem_keyword_ratio"] = (matches / len(required)) if required else 1.0
            result["task_complete"] = True
            self.last_action_result = "Postmortem submitted."
            return result

        if action.action_type == ActionType.IDENTIFY_SERVICE:
            self.last_action_result = f"Service identified as {action.service_name}."
            return result

        if action.action_type == ActionType.PROPOSE_MITIGATION:
            if action.command == self.current_scenario.get("ground_truth", {}).get("correct_mitigation_command"):
                result["task_complete"] = True
                self.last_action_result = "Mitigation accepted and incident stabilized."
            else:
                self.last_action_result = "Mitigation command executed but did not resolve incident."
            return result

        if action.action_type == ActionType.CLASSIFY_ALERT:
            result["task_complete"] = True
            self.last_action_result = "Alert classification submitted."
            return result

        return result

    def _unlock_next_step(self, step_id: str) -> None:
        ids = [step["step_id"] for step in self.runbook_steps]
        if step_id not in ids:
            return
        idx = ids.index(step_id)
        if idx + 1 < len(self.runbook_steps):
            self.runbook_steps[idx + 1]["is_available"] = True

    def _apply_dynamic_updates(self, step_id: str) -> None:
        updates = self.current_scenario.get("dynamic_state_updates", {}).get(step_id, {})
        new_logs = updates.get("new_logs", [])
        if new_logs:
            self.recent_logs = (self.recent_logs + new_logs)[-20:]
        metric_updates = updates.get("metric_updates", {})
        for service_name, metric_patch in metric_updates.items():
            for metric_name, payload in metric_patch.items():
                snapshot = MetricSnapshot(
                    service=service_name,
                    metric_name=metric_name,
                    value=float(payload.get("value", 0.0)),
                    unit=payload.get("unit", "unknown"),
                    timestamp=payload.get("timestamp", "2026-01-01T00:00:00Z"),
                )
                self.current_metrics.append(snapshot.model_dump())

    def _build_observation(self) -> IncidentObservation:
        postmortem_prompt = None
        if self.task_id == 3 and self._runbook_resolved():
            postmortem_prompt = (
                "The incident is resolved. Please write a postmortem summary covering what happened, "
                "impacted services, mitigation steps, and prevention actions."
            )
        return IncidentObservation(
            task_id=self.task_id,
            step_number=self.step_number,
            episode_id=self.episode_id,
            time_elapsed_seconds=self.time_elapsed_seconds,
            active_alerts=self.current_scenario.get("alerts", []),
            recent_logs=self.recent_logs,
            current_metrics=self.current_metrics,
            runbook_steps=[RunbookStep(**step) for step in self.runbook_steps],
            last_action_result=self.last_action_result,
            last_action_was_valid=self.last_action_was_valid,
            postmortem_prompt=postmortem_prompt,
            actions_remaining=max(0, self.max_steps - self.step_number),
        )

    def _runbook_resolved(self) -> bool:
        if not self.runbook_steps:
            return False
        for step in self.runbook_steps:
            if step.get("should_fail"):
                continue
            if not step.get("is_completed"):
                return False
        return True
