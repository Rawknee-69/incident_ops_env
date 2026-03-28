from incident_ops_env.models import ActionType, IncidentAction
from incident_ops_env.server.scenario_loader import list_scenarios_for_task, load_scenario_by_id, validate_scenario_data


def test_action_model_basic():
    action = IncidentAction(
        action_type=ActionType.CLASSIFY_ALERT,
        severity="P2",
        service_name="payment-service",
        pattern_type="database_overload",
    )
    assert action.action_type == ActionType.CLASSIFY_ALERT
    assert action.service_name == "payment-service"


def test_scenario_loader_by_task():
    task1 = list_scenarios_for_task(1, include_uploaded=False)
    task2 = list_scenarios_for_task(2, include_uploaded=False)
    task3 = list_scenarios_for_task(3, include_uploaded=False)
    assert len(task1) == 4
    assert len(task2) == 3
    assert len(task3) == 3


def test_scenario_loader_by_id():
    scenario = load_scenario_by_id("task2_medium_001")
    assert scenario["task_id"] == 2
    assert "ground_truth" in scenario

    payment = load_scenario_by_id("task1_payment_db_alert", expected_task_id=1)
    assert payment["scenario_id"] == "task1_payment_db_alert"


def test_scenario_task_mismatch_rejected():
    try:
        load_scenario_by_id("task2_medium_001", expected_task_id=1)
    except ValueError as exc:
        assert "does not match requested task_id" in str(exc)
        return
    raise AssertionError("Expected ValueError for mismatched task_id")


def test_validate_scenario_data_task3_requires_runbook():
    bad_scenario = {
        "task_id": 3,
        "scenario_id": "bad_task3",
        "alerts": [
            {
                "alert_id": "ALT-1",
                "title": "alert",
                "severity": "P1",
                "service": "checkout-service",
                "triggered_at": "2026-03-27T00:00:00Z",
                "metadata": {},
            }
        ],
        "ground_truth": {
            "correct_escalation_team": "database",
            "required_postmortem_keywords": ["database"],
            "steps_that_should_complete": ["step_1"],
            "step_that_should_escalate": "step_2",
        },
    }
    try:
        validate_scenario_data(bad_scenario)
    except ValueError as exc:
        assert "requires non-empty runbook list" in str(exc)
        return
    raise AssertionError("Expected ValueError when task3 runbook is missing")
