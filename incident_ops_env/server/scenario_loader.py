from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"


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


def load_scenario_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    _validate_common_scenario(data)
    return data


def load_scenario_by_id(scenario_id: str) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path.name}")
    return load_scenario_file(path)


def list_scenarios_for_task(task_id: int) -> list[dict[str, Any]]:
    if task_id not in (1, 2, 3):
        raise ValueError("task_id must be 1, 2, or 3.")
    prefix = f"task{task_id}_"
    scenarios: list[dict[str, Any]] = []
    for path in sorted(SCENARIO_DIR.glob(f"{prefix}*.json")):
        scenarios.append(load_scenario_file(path))
    if not scenarios:
        raise FileNotFoundError(f"No scenarios found for task_id={task_id}.")
    return scenarios
