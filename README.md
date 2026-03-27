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

## What is IncidentOpsEnv
IncidentOpsEnv simulates on-call production incidents with alerts, logs, metrics, runbooks, escalation, and postmortems.  
It is designed for reinforcement-learning style agent loops where each step produces structured observations and rewards.

## Why this matters for RL
The environment uses dense, task-aware rewards so agents can improve from partial progress instead of sparse pass/fail outcomes.  
Scenarios are deterministic with seed support for reproducible benchmarking.

## Observation Space
See `incident_ops_env/models.py` and `IncidentObservation`.

## Action Space
See `ActionType` and `IncidentAction` in `incident_ops_env/models.py`.

## The 3 Tasks
- Task 1: alert triage
- Task 2: root-cause analysis
- Task 3: full incident playbook

## Reward Function
Implemented in `incident_ops_env/server/reward.py`.

## Setup and Installation
```bash
uv venv .venv
uv pip install -e .[dev]
uv run uvicorn incident_ops_env.server.app:app --port 7860
```

## Baseline Scores
TBD

## License
MIT
