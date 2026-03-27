from incident_ops_env.models import ActionType, IncidentAction
from incident_ops_env.server.environment import IncidentOpsEnvironment


def test_reset_and_state_task1():
    env = IncidentOpsEnvironment()
    obs = env.reset(task_id=1, seed=42)
    assert obs.task_id == 1
    state = env.state()
    assert state.max_steps == 5
    assert state.step_number == 0


def test_invalid_action_penalty_does_not_advance():
    env = IncidentOpsEnvironment()
    env.reset(task_id=1, seed=42)
    result = env.step(IncidentAction(action_type=ActionType.FILTER_LOGS, log_service="checkout-service"))
    assert result.reward < 0
    assert result.observation.step_number == 0
    assert result.observation.last_action_was_valid is False


def test_task2_happy_path_grades():
    env = IncidentOpsEnvironment()
    env.reset(task_id=2, scenario_id="task2_medium_001")
    env.step(IncidentAction(action_type=ActionType.FILTER_LOGS, log_service="checkout-service"))
    env.step(IncidentAction(action_type=ActionType.GET_METRIC, metric_name="error_rate", service_name="checkout-service"))
    env.step(IncidentAction(action_type=ActionType.IDENTIFY_SERVICE, service_name="checkout-service"))
    env.step(
        IncidentAction(
            action_type=ActionType.PROPOSE_MITIGATION,
            command="kubectl rollout undo deployment/checkout-service",
        )
    )
    assert env.grade() == 1.0


def test_task3_escalation_does_not_end_episode_and_retry_penalized():
    env = IncidentOpsEnvironment()
    env.reset(task_id=3, scenario_id="task3_hard_001")

    failing_step_id = next(step["step_id"] for step in env.runbook_steps if step.get("should_fail"))
    for step in env.runbook_steps:
        if step["step_id"] == failing_step_id:
            step["is_available"] = True

    first_fail = env.step(
        IncidentAction(
            action_type=ActionType.EXECUTE_RUNBOOK_STEP,
            runbook_step_id=failing_step_id,
            command="wrong command",
        )
    )
    assert first_fail.done is False

    second_fail = env.step(
        IncidentAction(
            action_type=ActionType.EXECUTE_RUNBOOK_STEP,
            runbook_step_id=failing_step_id,
            command="still wrong",
        )
    )
    assert second_fail.info["reward_breakdown"].get("RETRY_FAILED_STEP", 0) < 0

    escalation = env.step(IncidentAction(action_type=ActionType.ESCALATE, escalation_team="database"))
    assert escalation.done is False
