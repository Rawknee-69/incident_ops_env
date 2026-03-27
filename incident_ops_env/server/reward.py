from __future__ import annotations

from incident_ops_env.models import ActionType, IncidentAction


REWARD_VALUES = {
    "CORRECT_SEVERITY": 0.10,
    "CORRECT_SERVICE": 0.10,
    "CORRECT_PATTERN": 0.10,
    "RELEVANT_LOG_QUERY": 0.05,
    "IRRELEVANT_LOG_QUERY": -0.02,
    "RELEVANT_METRIC_QUERY": 0.05,
    "CORRECT_SERVICE_ID": 0.15,
    "CORRECT_MITIGATION": 0.20,
    "WRONG_MITIGATION": -0.10,
    "RUNBOOK_STEP_CORRECT": 0.10,
    "RUNBOOK_STEP_WRONG_CMD": -0.05,
    "CORRECT_ESCALATION": 0.20,
    "WRONG_ESCALATION": -0.15,
    "ESCALATE_INSTEAD_OF_FIX": -0.10,
    "RETRY_FAILED_STEP": -0.05,
    "POSTMORTEM_COMPLETE": 0.15,
    "POSTMORTEM_INCOMPLETE": 0.05,
    "NO_OP_PENALTY": -0.03,
    "INVALID_ACTION_PENALTY": -0.05,
    "TIME_BONUS_FAST": 0.05,
    "TIME_BONUS_MEDIUM": 0.02,
}


def _add(breakdown: dict[str, float], key: str) -> None:
    breakdown[key] = breakdown.get(key, 0.0) + REWARD_VALUES[key]


def compute_step_reward(
    action: IncidentAction,
    action_result: dict,
    episode_state: dict,
    scenario_ground_truth: dict,
) -> tuple[float, dict]:
    breakdown: dict[str, float] = {}

    if action_result.get("invalid_action", False):
        _add(breakdown, "INVALID_ACTION_PENALTY")
    elif action.action_type == ActionType.NO_OP:
        _add(breakdown, "NO_OP_PENALTY")
    elif action.action_type == ActionType.CLASSIFY_ALERT:
        if action.severity == scenario_ground_truth.get("severity"):
            _add(breakdown, "CORRECT_SEVERITY")
        if action.service_name in (
            scenario_ground_truth.get("service"),
            scenario_ground_truth.get("root_cause_service"),
        ):
            _add(breakdown, "CORRECT_SERVICE")
        if action.pattern_type == scenario_ground_truth.get("pattern_type"):
            _add(breakdown, "CORRECT_PATTERN")
    elif action.action_type == ActionType.FILTER_LOGS:
        if action.log_service == scenario_ground_truth.get("root_cause_service"):
            _add(breakdown, "RELEVANT_LOG_QUERY")
        elif episode_state.get("irrelevant_log_queries", 0) >= 3:
            _add(breakdown, "IRRELEVANT_LOG_QUERY")
    elif action.action_type == ActionType.GET_METRIC:
        if action.metric_name in scenario_ground_truth.get("relevant_metrics", []):
            _add(breakdown, "RELEVANT_METRIC_QUERY")
    elif action.action_type == ActionType.IDENTIFY_SERVICE:
        if action.service_name == scenario_ground_truth.get("root_cause_service"):
            _add(breakdown, "CORRECT_SERVICE_ID")
    elif action.action_type == ActionType.PROPOSE_MITIGATION:
        if action.command == scenario_ground_truth.get("correct_mitigation_command"):
            _add(breakdown, "CORRECT_MITIGATION")
        else:
            wrong_count = episode_state.get("wrong_mitigation_count", 0)
            if wrong_count >= 2:
                _add(breakdown, "WRONG_MITIGATION")
    elif action.action_type == ActionType.EXECUTE_RUNBOOK_STEP:
        if action_result.get("step_success"):
            _add(breakdown, "RUNBOOK_STEP_CORRECT")
        else:
            _add(breakdown, "RUNBOOK_STEP_WRONG_CMD")
        if action_result.get("retried_failed_step"):
            _add(breakdown, "RETRY_FAILED_STEP")
    elif action.action_type == ActionType.ESCALATE:
        if action.escalation_team == scenario_ground_truth.get("correct_escalation_team"):
            _add(breakdown, "CORRECT_ESCALATION")
        elif action_result.get("escalated_instead_of_fix"):
            _add(breakdown, "ESCALATE_INSTEAD_OF_FIX")
        else:
            _add(breakdown, "WRONG_ESCALATION")
    elif action.action_type == ActionType.WRITE_POSTMORTEM:
        ratio = action_result.get("postmortem_keyword_ratio", 0.0)
        if ratio >= 1.0:
            _add(breakdown, "POSTMORTEM_COMPLETE")
        elif ratio > 0:
            _add(breakdown, "POSTMORTEM_INCOMPLETE")

    if action_result.get("episode_completed", False):
        max_steps = max(1, episode_state.get("max_steps", 1))
        used = episode_state.get("step_number", 0)
        ratio = used / max_steps
        if ratio < 0.5:
            _add(breakdown, "TIME_BONUS_FAST")
        elif ratio <= 0.75:
            _add(breakdown, "TIME_BONUS_MEDIUM")

    reward = sum(breakdown.values())
    reward = max(-0.30, min(0.30, reward))
    breakdown["total"] = reward
    return reward, breakdown
