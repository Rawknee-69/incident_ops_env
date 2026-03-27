from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    CLASSIFY_ALERT = "classify_alert"
    FILTER_LOGS = "filter_logs"
    GET_METRIC = "get_metric"
    IDENTIFY_SERVICE = "identify_service"
    PROPOSE_MITIGATION = "propose_mitigation"
    EXECUTE_RUNBOOK_STEP = "execute_runbook_step"
    ESCALATE = "escalate"
    WRITE_POSTMORTEM = "write_postmortem"
    NO_OP = "no_op"


class IncidentAction(BaseModel):
    action_type: ActionType = Field(description="The type of action the agent takes.")
    severity: Optional[Literal["P1", "P2", "P3"]] = Field(
        default=None,
        description="Alert severity classification.",
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Affected service or suspected root-cause service.",
    )
    pattern_type: Optional[
        Literal[
            "database_overload",
            "memory_leak",
            "network_partition",
            "deployment_regression",
            "traffic_spike",
            "disk_full",
            "authentication_failure",
            "unknown",
        ]
    ] = Field(default=None, description="Incident pattern classification.")
    log_service: Optional[str] = Field(default=None, description="Service to query logs for.")
    log_level: Optional[Literal["ERROR", "WARN", "INFO", "DEBUG"]] = Field(
        default=None,
        description="Log level to filter by.",
    )
    log_keyword: Optional[str] = Field(default=None, description="Keyword to search in logs.")
    metric_name: Optional[str] = Field(default=None, description="Metric name to fetch.")
    metric_window_minutes: Optional[int] = Field(
        default=None,
        description="Rolling window size in minutes for metrics query.",
    )
    command: Optional[str] = Field(default=None, description="Mitigation or runbook command.")
    escalation_team: Optional[
        Literal["database", "networking", "security", "platform", "management"]
    ] = Field(default=None, description="Team to escalate to.")
    escalation_reason: Optional[str] = Field(default=None, description="Escalation rationale.")
    postmortem_text: Optional[str] = Field(
        default=None,
        description="Postmortem content supplied by agent.",
    )
    runbook_step_id: Optional[str] = Field(
        default=None,
        description="Runbook step identifier for Task 3 execution.",
    )


class LogEntry(BaseModel):
    timestamp: str = Field(description="ISO 8601 timestamp string.")
    service: str = Field(description="Service emitting the log.")
    level: Literal["ERROR", "WARN", "INFO", "DEBUG"] = Field(description="Log level.")
    message: str = Field(description="Log message text.")


class MetricSnapshot(BaseModel):
    service: str = Field(description="Service name.")
    metric_name: str = Field(description="Metric key.")
    value: float = Field(description="Metric value.")
    unit: str = Field(description="Metric unit.")
    timestamp: str = Field(description="ISO 8601 timestamp string.")


class Alert(BaseModel):
    alert_id: str = Field(description="Alert identifier.")
    title: str = Field(description="Alert title.")
    severity: Literal["P1", "P2", "P3"] = Field(description="Alert severity.")
    service: str = Field(description="Service referenced by alert.")
    triggered_at: str = Field(description="ISO 8601 timestamp when alert fired.")
    metadata: dict = Field(description="Additional noisy metadata fields.")


class RunbookStep(BaseModel):
    step_id: str = Field(description="Runbook step ID.")
    description: str = Field(description="Human-readable step description.")
    expected_outcome: str = Field(description="Expected outcome text.")
    is_completed: bool = Field(default=False, description="Completion state.")
    is_available: bool = Field(default=True, description="Whether step is currently available.")
    should_fail: bool = Field(
        default=False,
        description="Whether this step should deliberately fail in scenario.",
    )
    correct_command: Optional[str] = Field(
        default=None,
        description="Command required for successful execution.",
    )


class IncidentObservation(BaseModel):
    task_id: int = Field(description="Active task id: 1, 2, or 3.")
    step_number: int = Field(description="Current episode step number.")
    episode_id: str = Field(description="Current episode UUID.")
    time_elapsed_seconds: float = Field(description="Simulated elapsed incident time.")
    active_alerts: list[Alert] = Field(description="Active alerts for current scenario.")
    recent_logs: list[LogEntry] = Field(
        default=[],
        description="Latest logs visible to the agent.",
    )
    current_metrics: list[MetricSnapshot] = Field(
        default=[],
        description="Latest metrics visible to the agent.",
    )
    runbook_steps: list[RunbookStep] = Field(
        default=[],
        description="Runbook state for Task 3.",
    )
    last_action_result: Optional[str] = Field(
        default=None,
        description="Human-readable result of the previous action.",
    )
    last_action_was_valid: bool = Field(
        default=True,
        description="Whether the previous action was valid.",
    )
    postmortem_prompt: Optional[str] = Field(
        default=None,
        description="Prompt shown when postmortem is required.",
    )
    actions_remaining: int = Field(description="Remaining action budget for episode.")


class IncidentState(BaseModel):
    episode_id: str = Field(description="Current episode UUID.")
    task_id: int = Field(description="Active task id.")
    scenario_id: str = Field(description="Scenario fixture id.")
    step_number: int = Field(description="Current step index.")
    max_steps: int = Field(description="Maximum allowed steps for this episode.")
    total_reward_so_far: float = Field(description="Accumulated reward.")
    is_done: bool = Field(description="Whether episode is complete.")
    done_reason: Optional[str] = Field(description="Episode completion reason.")
    reward_breakdown: dict = Field(description="Aggregated reward components.")


class IncidentStepResult(BaseModel):
    observation: IncidentObservation = Field(description="Observation after action.")
    reward: float = Field(description="Reward for single step.")
    done: bool = Field(description="Whether episode ended after this step.")
    info: dict = Field(description="Additional metadata including reward breakdown.")
