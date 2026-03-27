---
title: IncidentOpsEnv
emoji: 🚨
colorFrom: red
colorTo: orange
sdk: docker
app_port: 7860
tags:
  - openenv
  - reinforcement-learning
  - sre
  - incident-response
license: mit
---

# IncidentOpsEnv

OpenEnv-compatible environment for SRE incident response training.

[![PyPI](https://img.shields.io/pypi/v/incident-ops-env.svg)](https://pypi.org/project/incident-ops-env/)
[![Hugging Face Space](https://img.shields.io/badge/HuggingFace-Space-orange)](https://huggingface.co/spaces)
[![Discord](https://img.shields.io/badge/Discord-OpenEnv-5865F2)](https://discord.gg/)

## What is IncidentOpsEnv
IncidentOpsEnv simulates production incidents with noisy alerts, service logs, metrics, runbooks, escalation, and postmortems. The agent acts as an on-call engineer and must make high-quality decisions under action limits.

The environment is designed for RL loops: each action produces a structured observation, a bounded reward, done metadata, and a reproducible scenario state. This enables iterative training and apples-to-apples evaluation across runs.

## Why this matters for RL
Most incident-response problems are long-horizon and partially observable. Sparse pass/fail rewards make optimization unstable, so this environment uses dense, task-aware reward components that capture meaningful progress.

IncidentOpsEnv also supports deterministic seeded resets and fixed schemas, which improves benchmark quality and makes regression testing straightforward during model or prompt changes.

## Observation Space
| Field | Type | Description |
|---|---|---|
| `task_id` | `int` | Active task (`1`, `2`, or `3`) |
| `step_number` | `int` | Current step index in episode |
| `episode_id` | `str` | UUID for current episode |
| `time_elapsed_seconds` | `float` | Simulated elapsed incident time |
| `active_alerts` | `list[Alert]` | Alert feed visible to the agent |
| `recent_logs` | `list[LogEntry]` | Logs returned from queries or updates |
| `current_metrics` | `list[MetricSnapshot]` | Metric snapshots returned from queries |
| `runbook_steps` | `list[RunbookStep]` | Task-3 runbook state (availability/completion) |
| `last_action_result` | `str \| null` | Human-readable result for previous action |
| `last_action_was_valid` | `bool` | Whether previous action passed validation |
| `postmortem_prompt` | `str \| null` | Prompt shown once runbook resolution is reached |
| `actions_remaining` | `int` | Remaining step budget before max-step termination |

## Action Space
| Action Type | Purpose | Required Fields |
|---|---|---|
| `classify_alert` | Classify incident severity/service/pattern | `action_type`, `severity`, `service_name`, `pattern_type` |
| `filter_logs` | Query log database for a service | `action_type`, `log_service` |
| `get_metric` | Query service metric history | `action_type`, `metric_name` |
| `identify_service` | Declare suspected root-cause service | `action_type`, `service_name` |
| `propose_mitigation` | Propose remediation command | `action_type`, `command` |
| `execute_runbook_step` | Run Task-3 runbook step command | `action_type`, `runbook_step_id`, `command` |
| `escalate` | Escalate incident to human team | `action_type`, `escalation_team` (`escalation_reason` recommended) |
| `write_postmortem` | Submit postmortem text | `action_type`, `postmortem_text` |
| `no_op` | Take no action (penalized) | `action_type` |

## The 3 Tasks
### Task 1 — Alert Triage
- Difficulty: easy
- Max steps: `5`
- Goal: identify the real incident from noisy alerts and classify severity/service/pattern in one action
- Success: correct `classify_alert` values before step budget ends

### Task 2 — Root Cause Analysis
- Difficulty: medium
- Max steps: `15`
- Goal: inspect logs and metrics, identify root-cause service, and propose mitigation
- Success: useful evidence-gathering plus correct service identification and mitigation

### Task 3 — Full Incident Playbook
- Difficulty: hard
- Max steps: `25`
- Goal: execute runbook steps, handle failing step via escalation, and write postmortem
- Success: complete expected steps, escalate correctly, and submit keyword-complete postmortem

## Reward Function
Implemented in `incident_ops_env/server/reward.py`. Per-step rewards are clamped to `[-0.30, +0.30]`.

| Component | Value | Trigger |
|---|---:|---|
| `CORRECT_SEVERITY` | `+0.10` | Task 1 correct severity |
| `CORRECT_SERVICE` | `+0.10` | Task 1 correct service |
| `CORRECT_PATTERN` | `+0.10` | Task 1 correct pattern |
| `RELEVANT_LOG_QUERY` | `+0.05` | Task 2 log query on root-cause service |
| `IRRELEVANT_LOG_QUERY` | `-0.02` | Repeated irrelevant Task 2 log queries |
| `RELEVANT_METRIC_QUERY` | `+0.05` | Task 2 metric query in relevant metrics |
| `CORRECT_SERVICE_ID` | `+0.15` | Task 2 correct root-cause identification |
| `CORRECT_MITIGATION` | `+0.20` | Task 2 correct mitigation command |
| `WRONG_MITIGATION` | `-0.10` | Task 2 wrong/destructive mitigation |
| `RUNBOOK_STEP_CORRECT` | `+0.10` | Task 3 correct runbook step execution |
| `RUNBOOK_STEP_WRONG_CMD` | `-0.05` | Task 3 wrong runbook command |
| `CORRECT_ESCALATION` | `+0.20` | Task 3 escalation to expected team |
| `WRONG_ESCALATION` | `-0.15` | Task 3 unnecessary/wrong escalation |
| `ESCALATE_INSTEAD_OF_FIX` | `-0.10` | Escalates when step should be fixed |
| `RETRY_FAILED_STEP` | `-0.05` | Repeats known failing step |
| `POSTMORTEM_COMPLETE` | `+0.15` | Postmortem covers required keywords |
| `POSTMORTEM_INCOMPLETE` | `+0.05` | Postmortem present but incomplete |
| `NO_OP_PENALTY` | `-0.03` | `no_op` action |
| `INVALID_ACTION_PENALTY` | `-0.05` | Invalid action payload/field usage |
| `TIME_BONUS_FAST` | `+0.05` | Task completion in <50% max steps |
| `TIME_BONUS_MEDIUM` | `+0.02` | Task completion in 50-75% max steps |

## Setup and Installation
```bash
uv venv .venv
uv pip install -e .[dev]
uv run uvicorn incident_ops_env.server.app:app --port 7860
```

### Docker
```bash
docker build -f incident_ops_env/server/Dockerfile -t incident-ops-env .
docker run -p 7860:7860 incident-ops-env
```

### Client Usage
```python
from incident_ops_env import IncidentAction, IncidentOpsEnv

# Use as async client against running server
```

### Baseline
```bash
# Requires OPENAI_API_KEY or GEMINI_API_KEY
python baseline.py
```

For deterministic/no-key runs (useful for reproducibility checks):

```bash
BASELINE_PROVIDER=scripted .venv/bin/python baseline.py
```

### Phase Readiness Check

Run the automated gate checks for Phase 1 plus readiness notes for Phases 2 and 3:

```bash
.venv/bin/python scripts/phase_readiness.py --runs 3
```

If Docker is unavailable in your shell session, skip that check:

```bash
.venv/bin/python scripts/phase_readiness.py --runs 3 --skip-docker
```

## Baseline Scores
TBD

## License
MIT
