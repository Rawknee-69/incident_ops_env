from __future__ import annotations


def grade_task1(episode_history: list[dict], ground_truth: dict) -> float:
    for action in episode_history:
        if action.get("action_type") == "classify_alert":
            score = 0.0
            if action.get("severity") == ground_truth.get("severity"):
                score += 0.34
            if action.get("service_name") == ground_truth.get("service"):
                score += 0.33
            if action.get("pattern_type") == ground_truth.get("pattern_type"):
                score += 0.33
            return round(score, 2)
    return 0.0


def grade_task2(episode_history: list[dict], ground_truth: dict) -> float:
    score = 0.0
    root_service = ground_truth.get("root_cause_service")
    relevant_metrics = set(ground_truth.get("relevant_metrics", []))

    if any(
        a.get("action_type") == "filter_logs" and a.get("log_service") == root_service
        for a in episode_history
    ):
        score += 0.2

    if any(
        a.get("action_type") == "get_metric" and a.get("metric_name") in relevant_metrics
        for a in episode_history
    ):
        score += 0.1

    for action in episode_history:
        if (
            action.get("action_type") == "identify_service"
            and action.get("service_name") == root_service
        ):
            score += 0.3
            break

    for action in episode_history:
        if (
            action.get("action_type") == "propose_mitigation"
            and action.get("command") == ground_truth.get("correct_mitigation_command")
        ):
            score += 0.4
            break

    return round(score, 2)


def grade_task3(episode_history: list[dict], ground_truth: dict) -> float:
    score = 0.0

    should_complete = set(ground_truth.get("steps_that_should_complete", []))
    completed_steps = {
        a.get("runbook_step_id")
        for a in episode_history
        if a.get("action_type") == "execute_runbook_step" and a.get("_was_successful", False)
    }
    if should_complete:
        step_score_each = 0.40 / len(should_complete)
        score += len(completed_steps & should_complete) * step_score_each

    escalated_correctly = any(
        a.get("action_type") == "escalate"
        and a.get("escalation_team") == ground_truth.get("correct_escalation_team")
        for a in episode_history
    )
    if escalated_correctly:
        score += 0.30

    retries = sum(
        1
        for a in episode_history
        if a.get("action_type") == "execute_runbook_step"
        and a.get("runbook_step_id") == ground_truth.get("step_that_should_escalate")
    )
    if retries > 1:
        score -= 0.10

    required = [kw.lower() for kw in ground_truth.get("required_postmortem_keywords", [])]
    for action in episode_history:
        if action.get("action_type") == "write_postmortem" and action.get("postmortem_text"):
            text = action["postmortem_text"].lower()
            if required:
                matches = sum(1 for kw in required if kw in text)
                score += 0.30 * (matches / len(required))
            else:
                score += 0.30
            break

    return round(min(max(score, 0.0), 1.0), 2)
