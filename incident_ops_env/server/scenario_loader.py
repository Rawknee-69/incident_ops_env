from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from incident_ops_env.server.scenario_registry import ScenarioRegistry


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
registry = ScenarioRegistry()


def _validate_common_scenario(scenario: dict[str, Any]) -> None:
    required = {"task_id", "scenario_id", "alerts", "ground_truth"}
    missing = required - set(scenario.keys())
    if missing:
        raise ValueError(f"Scenario is missing required keys: {sorted(missing)}")
    if scenario["task_id"] not in (1, 2, 3):
        raise ValueError("task_id must be 1, 2, or 3.")
    if not isinstance(scenario["alerts"], list) or not scenario["alerts"]:
        raise ValueError("alerts must be a non-empty list.")
    if not isinstance(scenario["ground_truth"], dict):
        raise ValueError("ground_truth must be an object.")


def _validate_task_specific(scenario: dict[str, Any]) -> None:
    task_id = scenario["task_id"]
    ground_truth = scenario["ground_truth"]
    if task_id == 1:
        required = {"severity", "service", "pattern_type"}
        missing = required - set(ground_truth.keys())
        if missing:
            raise ValueError(f"Task 1 ground_truth missing keys: {sorted(missing)}")
        return
    if task_id == 2:
        if "log_database" not in scenario or not isinstance(scenario["log_database"], dict):
            raise ValueError("Task 2 scenario requires log_database object.")
        if "metric_database" not in scenario or not isinstance(scenario["metric_database"], dict):
            raise ValueError("Task 2 scenario requires metric_database object.")
        required = {"root_cause_service", "correct_mitigation_command", "relevant_metrics"}
        missing = required - set(ground_truth.keys())
        if missing:
            raise ValueError(f"Task 2 ground_truth missing keys: {sorted(missing)}")
        return
    if task_id == 3:
        if "runbook" not in scenario or not isinstance(scenario["runbook"], list) or not scenario["runbook"]:
            raise ValueError("Task 3 scenario requires non-empty runbook list.")
        required = {
            "correct_escalation_team",
            "required_postmortem_keywords",
            "steps_that_should_complete",
            "step_that_should_escalate",
        }
        missing = required - set(ground_truth.keys())
        if missing:
            raise ValueError(f"Task 3 ground_truth missing keys: {sorted(missing)}")


def validate_scenario_data(scenario: dict[str, Any], expected_task_id: int | None = None) -> dict[str, Any]:
    _validate_common_scenario(scenario)
    _validate_task_specific(scenario)
    if expected_task_id is not None and scenario["task_id"] != expected_task_id:
        raise ValueError(
            f"Scenario task_id={scenario['task_id']} does not match requested task_id={expected_task_id}."
        )
    return scenario


def load_scenario_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_scenario_data(data)


def load_scenario_by_id(scenario_id: str, expected_task_id: int | None = None) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{scenario_id}.json"
    if path.exists():
        return validate_scenario_data(load_scenario_file(path), expected_task_id=expected_task_id)
    uploaded_path = registry.get_uploaded_path(scenario_id)
    if uploaded_path is not None:
        return validate_scenario_data(load_scenario_file(uploaded_path), expected_task_id=expected_task_id)
    raise FileNotFoundError(f"Scenario file not found: {scenario_id}.json")


def list_scenarios_for_task(task_id: int, include_uploaded: bool = False) -> list[dict[str, Any]]:
    if task_id not in (1, 2, 3):
        raise ValueError("task_id must be 1, 2, or 3.")
    prefix = f"task{task_id}_"
    scenarios: list[dict[str, Any]] = []
    for path in sorted(SCENARIO_DIR.glob(f"{prefix}*.json")):
        scenarios.append(load_scenario_file(path))
    if include_uploaded:
        for scenario_id in registry.list_uploaded_ids():
            uploaded_path = registry.get_uploaded_path(scenario_id)
            if uploaded_path is None:
                continue
            scenario = load_scenario_file(uploaded_path)
            if scenario["task_id"] == task_id:
                scenarios.append(scenario)
    if not scenarios:
        raise FileNotFoundError(f"No scenarios found for task_id={task_id}.")
    return scenarios
