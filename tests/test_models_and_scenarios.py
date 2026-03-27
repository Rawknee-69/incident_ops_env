from incident_ops_env.models import ActionType, IncidentAction
from incident_ops_env.server.scenario_loader import list_scenarios_for_task, load_scenario_by_id


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
    task1 = list_scenarios_for_task(1)
    task2 = list_scenarios_for_task(2)
    task3 = list_scenarios_for_task(3)
    assert len(task1) == 3
    assert len(task2) == 3
    assert len(task3) == 3


def test_scenario_loader_by_id():
    scenario = load_scenario_by_id("task2_medium_001")
    assert scenario["task_id"] == 2
    assert "ground_truth" in scenario
