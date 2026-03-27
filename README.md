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
docker build -f Dockerfile -t incident-ops-env .
docker run -p 7860:7860 incident-ops-env
```

### Gradio Observability UI

The app exposes a Gradio UI at `/ui` with:

- Episode runner (reset/step/state)
- Task and grader explorer
- Baseline trigger/inspection
- Live metrics monitor (`/metrics` + `/ws/metrics`)
- Scenario upload and validation tools

When running locally:

```bash
open http://localhost:7860/ui
```

### Client Usage

Install the client from your running HuggingFace Space:

```bash
pip install git+https://huggingface.co/spaces/YOUR-USERNAME/incident_ops_env
```

Then use it:

```python
import asyncio
from incident_ops_env import IncidentAction, IncidentOpsEnv

async def main():
    async with IncidentOpsEnv(base_url="https://YOUR-USERNAME-incident-ops-env.hf.space") as env:
        # Reset to Task 1 with a fixed seed for reproducibility
        result = await env.reset(task_id=1, seed=42)
        print("Alerts:", len(result.observation.active_alerts))

        # Take an action
        action = IncidentAction(
            action_type="classify_alert",
            severity="P2",
            service_name="payment-service",
            pattern_type="database_overload",
        )
        result = await env.step(action)
        print("Reward:", result.reward)
        print("Done:", result.done)

asyncio.run(main())
```

Synchronous usage is also supported:

```python
from incident_ops_env import IncidentAction, IncidentOpsEnv

with IncidentOpsEnv(base_url="https://YOUR-USERNAME-incident-ops-env.hf.space").sync() as env:
    result = env.reset(task_id=1, seed=42)
    action = IncidentAction(action_type="no_op")
    result = env.step(action)
    print(result.reward)
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

For deployed HF Space smoke checks:

```bash
.venv/bin/python scripts/phase_readiness.py --runs 3 --space-url "https://<your-space>.hf.space"
```

### HF Smoke Test Commands

```bash
curl -sS https://<your-space>.hf.space/health
curl -sS https://<your-space>.hf.space/tasks
curl -sS -X POST https://<your-space>.hf.space/reset -H "content-type: application/json" -d '{"task_id":1,"seed":42}'
```

## Baseline Scores
Scripted deterministic baseline (`BASELINE_PROVIDER=scripted`) reproducibly reports:

- `task_1`: `0.67`
- `task_2`: `0.50`
- `task_3`: `0.57`
- average: `0.58`

These values come from fixed seeds in `baseline.py` and can be re-run locally via:

```bash
BASELINE_PROVIDER=scripted .venv/bin/python baseline.py
```

## License
MIT
