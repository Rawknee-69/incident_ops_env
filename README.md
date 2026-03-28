---
title: IncidentOpsEnv
emoji: 🚨
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
tags:
  - openenv
  - reinforcement-learning
  - sre
  - incident-response
license: mit
---

# IncidentOpsEnv 🚨

**An OpenEnv-compliant RL environment that trains AI agents to handle production incidents.**

Instead of playing games, agents here act as on-call Site Reliability Engineers — reading alerts, querying logs, checking metrics, running runbook steps, escalating failures, and writing postmortems. Every action returns a reward signal. Agents get better through experience.

**Live Space:** `https://kyoiske-incident-ops-env.hf.space`  
**Gradio UI:** `https://kyoiske-incident-ops-env.hf.space/ui`  
**API Docs:** `https://kyoiske-incident-ops-env.hf.space/docs`

---

## What This Is — The Short Version

Think of it like this:

- **The environment** = a fake production system having an outage
- **The agent** = an on-call engineer who gets paged at 3am
- **The episode** = one incident from first alert to resolution
- **The reward** = how well the agent diagnosed and fixed it

The agent can't see the answer. It has to call actions (query logs, check metrics, classify alerts) to gather evidence, then act on that evidence. Every correct step earns reward. Wrong guesses, wasted steps, and destructive actions lose reward.

---

## The 3 Tasks

### Task 1 — Alert Triage `[Easy, 5 steps max]`

A batch of 3–8 alerts arrives. Most are noise — resolved alerts, maintenance notices, unrelated services. One is the real incident. The agent must identify it and classify:
- What **severity** is it? (P1 / P2 / P3)
- Which **service** is affected?
- What **pattern** does it match? (database overload, memory leak, deployment regression, etc.)

One action ends the episode. The agent either gets it right or doesn't. Good for testing alert reading ability.

**Only actions valid here:** `classify_alert`, `no_op`

---

### Task 2 — Root Cause Analysis `[Medium, 15 steps max]`

A service is failing. The agent sees alerts and a list of recent deployments. It must investigate by querying the environment's log and metric databases, narrow down the root cause, and propose the correct fix command.

**Investigation actions:**
- `filter_logs` — query logs for a specific service (returns up to 20 matching lines)
- `get_metric` — fetch a metric time-series for a specific service

**Diagnosis actions:**
- `identify_service` — declare which service is the root cause
- `propose_mitigation` — propose the exact kubectl/bash command to fix it

The agent must both investigate AND diagnose correctly to score high. Just guessing the fix without looking at evidence works rarely. Correct mitigation command ends the episode.

---

### Task 3 — Full Incident Playbook `[Hard, 25 steps max]`

Multiple services are failing. The agent has a runbook — an ordered list of steps to execute. Steps unlock sequentially. One step is deliberately broken and will always fail. The agent must:

1. Execute steps in order using `execute_runbook_step`
2. Recognize when a step fails and `escalate` to the right specialist team instead of retrying
3. Once all steps are resolved, write a postmortem with `write_postmortem`

This tests long-horizon planning, failure handling, and communication — the hardest things about real incident response.

---

## All 9 Actions

| Action | Valid in | Required fields | What it does |
|---|---|---|---|
| `classify_alert` | T1, T2, T3 | `severity`, `service_name`, `pattern_type` | Classifies the incident. Ends Task 1 immediately |
| `filter_logs` | T2, T3 | `log_service` | Queries logs. Optional: `log_level`, `log_keyword` |
| `get_metric` | T2, T3 | `service_name`, `metric_name` | Fetches metric time-series. Optional: `metric_window_minutes` |
| `identify_service` | T2, T3 | `service_name` | Declares root cause service |
| `propose_mitigation` | T2 | `command` | Proposes fix command. Ends Task 2 if correct |
| `execute_runbook_step` | T3 | `runbook_step_id`, `command` | Runs a runbook step. Must use exact correct command |
| `escalate` | T3 | `escalation_team`, `escalation_reason` | Escalates a failing step to a specialist team |
| `write_postmortem` | T3 | `postmortem_text` | Submits postmortem. Ends Task 3 |
| `no_op` | All | none | Does nothing. Penalized -0.03 |

### Valid values for enum fields

**severity:** `P1` (critical outage) · `P2` (major degradation) · `P3` (minor issue)

**pattern_type:** `database_overload` · `memory_leak` · `network_partition` · `deployment_regression` · `traffic_spike` · `disk_full` · `authentication_failure` · `unknown`

**escalation_team:** `database` · `networking` · `security` · `platform` · `management`

**log_level:** `ERROR` · `WARN` · `INFO` · `DEBUG`

---

## Reward Signal — Every Component

Rewards are computed per step and clamped to `[-0.30, +0.30]`. Total episode reward accumulates across all steps.

| Component | Value | When it fires |
|---|---|---|
| `CORRECT_SEVERITY` | +0.10 | Task 1: severity matches ground truth |
| `CORRECT_SERVICE` | +0.10 | Task 1: service_name matches ground truth |
| `CORRECT_PATTERN` | +0.10 | Task 1: pattern_type matches ground truth |
| `RELEVANT_LOG_QUERY` | +0.05 | Task 2/3: filter_logs targets the root cause service |
| `IRRELEVANT_LOG_QUERY` | -0.02 | Task 2/3: 3+ irrelevant log queries made |
| `RELEVANT_METRIC_QUERY` | +0.05 | Task 2/3: metric_name is in the relevant metrics list |
| `CORRECT_SERVICE_ID` | +0.15 | Task 2/3: identify_service matches root cause |
| `CORRECT_MITIGATION` | +0.20 | Task 2: propose_mitigation matches correct command |
| `WRONG_MITIGATION` | -0.10 | Task 2: wrong command on 2nd+ attempt |
| `RUNBOOK_STEP_CORRECT` | +0.10 | Task 3: runbook step executed with correct command |
| `RUNBOOK_STEP_WRONG_CMD` | -0.05 | Task 3: wrong command for a runbook step |
| `CORRECT_ESCALATION` | +0.30 | Task 3: escalated to correct team after step failure |
| `WRONG_ESCALATION` | -0.15 | Task 3: unnecessary escalation |
| `ESCALATE_INSTEAD_OF_FIX` | -0.10 | Task 3: escalated a step that should be fixed |
| `RETRY_FAILED_STEP` | -0.05 | Task 3: retried a step already flagged as failed |
| `POSTMORTEM_COMPLETE` | +0.30 | Task 3: postmortem covers all required keywords |
| `POSTMORTEM_INCOMPLETE` | +0.05 to +0.29 | Task 3: partial keyword coverage |
| `NO_OP_PENALTY` | -0.03 | Any task: no_op action taken |
| `INVALID_ACTION_PENALTY` | -0.05 | Any task: action missing required fields |
| `TIME_BONUS_FAST` | +0.05 | Episode ends in under 50% of max steps |
| `TIME_BONUS_MEDIUM` | +0.02 | Episode ends in 50–75% of max steps |

---

## How to Use It

### From Python (HTTP — Simplest)

```python
import httpx, json

BASE = "https://kyoiske-incident-ops-env.hf.space"

# 1. Start an episode
r = httpx.post(f"{BASE}/reset", json={"task_id": 1, "seed": 42})
session_id = r.json()["session_id"]
obs = r.json()["observation"]

# 2. Read the alerts and decide
action = {
    "action_type": "classify_alert",
    "severity": "P2",
    "service_name": "payment-service",
    "pattern_type": "database_overload"
}

# 3. Step
r = httpx.post(f"{BASE}/step",
    json={"action": action},
    headers={"X-Session-ID": session_id})

print("reward:", r.json()["reward"])   # how well you did this step
print("done:", r.json()["done"])        # True = episode over

# 4. Get final score (0.0 to 1.0) once done=True
r = httpx.post(f"{BASE}/grader", json={"session_id": session_id})
print("score:", r.json()["score"])
```

### From Python (OpenAI agent loop)

```python
import os, json, httpx
from openai import OpenAI

BASE = "https://kyoiske-incident-ops-env.hf.space"
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

r = httpx.post(f"{BASE}/reset", json={"task_id": 2, "seed": 42})
session_id = r.json()["session_id"]
obs = r.json()["observation"]
done = False

while not done:
    # Ask the LLM what to do next
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are an SRE. Respond only with a JSON action object."},
            {"role": "user", "content": json.dumps(obs)}
        ]
    )
    action = json.loads(response.choices[0].message.content)

    # Take the action
    r = httpx.post(f"{BASE}/step",
        json={"action": action},
        headers={"X-Session-ID": session_id})
    obs = r.json()["observation"]
    done = r.json()["done"]

# Score
r = httpx.post(f"{BASE}/grader", json={"session_id": session_id})
print("Final score:", r.json()["score"])
```

### From Python (Gemini agent loop)

```python
import os, json, httpx
import google.generativeai as genai

BASE = "https://kyoiske-incident-ops-env.hf.space"
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel(
    "gemini-2.0-flash",
    system_instruction="You are an SRE. Respond only with a valid JSON action object."
)

r = httpx.post(f"{BASE}/reset", json={"task_id": 2, "seed": 42})
session_id = r.json()["session_id"]
obs = r.json()["observation"]
done = False

while not done:
    response = model.generate_content(json.dumps(obs))
    action = json.loads(response.text)
    r = httpx.post(f"{BASE}/step",
        json={"action": action},
        headers={"X-Session-ID": session_id})
    obs = r.json()["observation"]
    done = r.json()["done"]

r = httpx.post(f"{BASE}/grader", json={"session_id": session_id})
print("Final score:", r.json()["score"])
```

### From Python (WebSocket — faster for long training loops)

```python
import asyncio, json, websockets

async def run():
    async with websockets.connect("wss://kyoiske-incident-ops-env.hf.space/ws") as ws:
        # Reset
        await ws.send(json.dumps({"type": "reset", "task_id": 1, "seed": 42}))
        data = json.loads(await ws.recv())
        obs = data["observation"]

        # Step
        await ws.send(json.dumps({
            "type": "step",
            "action": {
                "action_type": "classify_alert",
                "severity": "P2",
                "service_name": "payment-service",
                "pattern_type": "database_overload"
            }
        }))
        data = json.loads(await ws.recv())
        print("reward:", data["reward"], "done:", data["done"])

        # Check state anytime
        await ws.send(json.dumps({"type": "state"}))
        state = json.loads(await ws.recv())
        print("step_number:", state["step_number"])

asyncio.run(run())
```

### From curl (quick tests)

```bash
BASE=https://kyoiske-incident-ops-env.hf.space

# Health check
curl $BASE/health

# Start Task 1 episode
curl -X POST $BASE/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id": 1, "seed": 42}'

# Take an action (replace SESSION_ID with value from reset response)
curl -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: SESSION_ID" \
  -d '{"action": {"action_type": "classify_alert", "severity": "P2", "service_name": "payment-service", "pattern_type": "database_overload"}}'

# Get final score
curl -X POST $BASE/grader \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID"}'

# See live metrics
curl $BASE/metrics

# List available scenarios
curl $BASE/scenarios

# Get all action schemas
curl $BASE/tasks
```

---

## Every Endpoint Explained

### Read-only (no session needed)

| Endpoint | What it returns |
|---|---|
| `GET /` | Live dashboard — server stats, metrics charts, episode runner, Gradio link |
| `GET /health` | `{"status":"healthy","environment":"incident_ops_env","version":"1.0.0"}` |
| `GET /metadata` | Environment name, description, author, task count — required by OpenEnv validator |
| `GET /schema` | Full JSON schemas for IncidentAction, IncidentObservation, IncidentState |
| `GET /tasks` | All 3 tasks with descriptions, max_steps, and full action schemas |
| `GET /scenarios` | Lists all 10 built-in scenarios by task, plus any user-uploaded ones |
| `GET /metrics` | Live server metrics — episodes, scores, steps/sec, rewards, latency |
| `GET /metrics/prometheus` | Same metrics in Prometheus text format for scraping |
| `GET /openapi.json` | Full OpenAPI spec for all endpoints |
| `GET /docs` | Swagger UI — interactive API explorer |
| `GET /redoc` | ReDoc API documentation |
| `POST /mcp` | MCP JSON-RPC 2.0 — `tools/list` returns all 9 actions as MCP tools |

### Episode control (session required)

These require an `X-Session-ID` header. Get your session ID from `/reset`.

| Endpoint | Method | What it does |
|---|---|---|
| `/reset` | POST | Starts a new episode. Returns initial observation + session_id |
| `/step` | POST | Takes one action. Returns observation + reward + done + info |
| `/state` | GET | Returns episode metadata (step count, total reward, done status) |
| `/grader` | POST | Returns final score 0.0–1.0. Only works after `done=True` |

### Utility

| Endpoint | Method | What it does |
|---|---|---|
| `/baseline` | POST | Runs the scripted baseline against all 3 tasks. Returns reproducible scores |
| `/scenarios/validate` | POST | Validates a scenario JSON before uploading |
| `/scenarios/upload` | POST | Uploads a custom scenario for the environment to use |
| `/ui` | GET | Redirects to the Gradio observability UI at `/ui/` |

### WebSocket

| Endpoint | Protocol | What it does |
|---|---|---|
| `/ws` | WebSocket | Persistent session. Send `reset`/`step`/`state` messages |
| `/ws/metrics` | WebSocket | Live event stream — pushes every step, episode start/end in real time |

---

## Understanding the Observation

Every `/step` and `/reset` response contains an `observation` object. Here's what every field means:

```json
{
  "task_id": 1,               // Which task is running (1, 2, or 3)
  "step_number": 0,           // How many steps taken so far
  "episode_id": "uuid...",    // Unique ID for this episode
  "time_elapsed_seconds": 0,  // Simulated incident time (adds 30s per step)
  "actions_remaining": 5,     // Steps left before episode times out
  
  "active_alerts": [...],     // The alert feed. Mix of real + noise alerts
  "recent_logs": [...],       // Logs returned from your last filter_logs action
  "current_metrics": [...],   // Metrics returned from your last get_metric action
  "runbook_steps": [...],     // Task 3 only: current runbook state
  
  "last_action_result": "...",    // Plain English result of your last action
  "last_action_was_valid": true,  // False if your action was missing required fields
  
  "postmortem_prompt": null   // Task 3 only: becomes non-null when postmortem is needed
}
```

**Reading the alerts** — `active_alerts` is an array. Most are noise. Clues:
- Check `"resolved": false` in metadata (resolved alerts are noise)
- Check `"type": "maintenance"` (maintenance notices are noise)
- The real alert has a non-resolved status and a title that describes an actual failure

**Reading logs** — `recent_logs` is empty until you call `filter_logs`. Each log entry has `timestamp`, `service`, `level` (`ERROR`/`WARN`/`INFO`/`DEBUG`), and `message`.

**Reading metrics** — `current_metrics` is empty until you call `get_metric`. Each snapshot has `service`, `metric_name`, `value`, `unit`, and `timestamp`.

**Reading runbook_steps** — Each step has `step_id`, `description`, `expected_outcome`, `is_completed` (bool), and `is_available` (bool). Steps unlock in order. A step with `is_available: false` cannot be executed yet.

---

## Understanding the `/metrics` Response

Call `GET /metrics` to see what's happening inside the environment in real time.

```json
{
  "server": {
    "uptime_seconds": 3842.1,
    "uptime_human": "1h 4m 2s",
    "active_sessions": 2,           // How many agents are running episodes right now
    "active_sessions_detail": {...}  // Per-session: task_id, step count, start time
  },
  "episodes": {
    "started": 47,     // Total episodes started since server start
    "completed": 45,   // Episodes that finished (hit done=True or max_steps)
    "by_task": {"1": 20, "2": 15, "3": 12}  // Breakdown by task
  },
  "scores": {
    "average_by_task": {
      "task_1": 0.812,   // Average grader score across all Task 1 episodes
      "task_2": 0.534,
      "task_3": 0.421
    },
    "distribution": {
      "0.0-0.2": 3,    // How many episodes scored in each range
      "0.2-0.4": 7,
      "0.4-0.6": 12,
      "0.6-0.8": 18,
      "0.8-1.0": 5
    },
    "recent": [...]   // Last 10 completed episodes with score + task + steps
  },
  "steps": {
    "total": 312,            // Total steps taken across all episodes
    "per_second": 0.8,       // Current throughput (last 60s)
    "invalid_action_rate": 0.03,   // Fraction of actions that were invalid
    "no_op_rate": 0.01,            // Fraction of actions that were no_op
    "action_type_distribution": {  // How often each action type has been used
      "classify_alert": 20,
      "filter_logs": 87,
      "get_metric": 64,
      ...
    },
    "recent_rewards": [-0.05, 0.05, 0.15, ...],  // Last 20 per-step rewards
    "avg_reward": 0.06                             // Average reward per step
  },
  "api": {
    "request_counts": {"/reset": 47, "/step": 312, ...},
    "latency_stats": {
      "/step": {"p50": 2.1, "count": 312}  // Median latency in ms
    }
  }
}
```

**What to watch:**
- `active_sessions` — how many concurrent agents are running
- `average_by_task` — is your agent improving over episodes?
- `invalid_action_rate` — if this is high, your agent is sending malformed actions
- `action_type_distribution` — is your agent using a variety of actions or stuck on one?
- `recent_rewards` — are step rewards trending positive or negative?

The live dashboard at `/` shows all of this graphically and refreshes every 2 seconds automatically.

---

## The Live Dashboard (`/`)

Open `https://kyoiske-incident-ops-env.hf.space` in a browser. You'll see:

**Live Metrics Dashboard** (top half, auto-refreshes every 2 seconds):
- Server uptime and active session count
- Episode counters (started / completed / by task)
- Average score bars per task
- Score distribution histogram
- Recent episodes feed (last 10, with score + task + steps used)
- Action type distribution (which tools agents use most)
- Endpoint latency table

**Environment Workbench** (bottom half, 5 tabs):

**Episode Runner tab** — Call the API from your browser without writing code. Paste JSON actions, click Run Step, see the observation and reward. Great for exploring what a scenario looks like.

**Tasks + Grader tab** — Load the full task schema (shows exactly what actions are valid, what fields are required). Run the grader for any session_id.

**Metrics tab** — Raw JSON dump of `/metrics` with a Refresh button.

**Baseline tab** — Trigger a full baseline run and see scores. Requires an API key to be configured on the server (or set `BASELINE_PROVIDER=scripted` in Space secrets to run without a key).

**Scenario Upload/Validation tab** — Upload your own scenario JSON files. The environment validates them before accepting. Useful for testing custom incident scenarios.

---

## The Gradio UI (`/ui`)

Go to `https://kyoiske-incident-ops-env.hf.space/ui` for a more polished interface with the same 5 tabs. Gradio renders responses with syntax highlighting and handles the session ID automatically between calls.

Use this when:
- You want to explore the environment interactively without curl
- You want to watch an episode play out step by step
- You want to manually test a specific scenario

---

## Built-in Scenarios

The environment ships with 10 pre-built scenarios. Each is a complete JSON fixture with alerts, logs, metrics, and a ground truth answer key.

| Scenario | Task | Incident |
|---|---|---|
| `task1_easy_001` | 1 | Payment service database overload (P2) |
| `task1_easy_002` | 1 | API gateway network partition (P1) |
| `task1_easy_003` | 1 | Search service memory leak (P2) |
| `task1_payment_db_alert` | 1 | Payment service database overload variant |
| `task2_medium_001` | 2 | Checkout service deployment regression → rollback |
| `task2_medium_002` | 2 | Auth service token cache corruption → restart |
| `task2_medium_003` | 2 | Inventory service disk full → log archive cleanup |
| `task3_hard_001` | 3 | Checkout + database pool exhaustion with escalation |
| `task3_hard_002` | 3 | Payments outage with upstream network degradation |
| `task3_hard_003` | 3 | Search platform incident with failing DB diagnostics |

To use a specific scenario (useful for reproducible evals):

```python
httpx.post(f"{BASE}/reset", json={"task_id": 2, "scenario_id": "task2_medium_001"})
```

To use a random scenario with a fixed seed (reproducible but varied):

```python
httpx.post(f"{BASE}/reset", json={"task_id": 1, "seed": 42})
```

---

## Uploading Custom Scenarios

You can add your own incident scenarios. They must follow the JSON schema for the task level.

**Validate first:**
```bash
curl -X POST https://kyoiske-incident-ops-env.hf.space/scenarios/validate \
  -H "Content-Type: application/json" \
  -d '{"scenario": { ...your scenario JSON... }}'
```

**Then upload:**
```bash
curl -X POST https://kyoiske-incident-ops-env.hf.space/scenarios/upload \
  -H "Content-Type: application/json" \
  -d '{"scenario": { ...your scenario JSON... }}'
```

Minimum Task 1 scenario structure:
```json
{
  "task_id": 1,
  "scenario_id": "my_custom_scenario",
  "alerts": [
    {
      "alert_id": "ALT-001",
      "title": "5xx spike on payment-service",
      "severity": "P2",
      "service": "payment-service",
      "triggered_at": "2026-01-01T03:00:00Z",
      "metadata": {"resolved": false}
    }
  ],
  "ground_truth": {
    "severity": "P2",
    "service": "payment-service",
    "pattern_type": "database_overload"
  }
}
```

Note: uploaded scenarios are stored in `/tmp` inside the Docker container. They will be lost when the Space restarts. Built-in scenarios (in `incident_ops_env/scenarios/`) persist forever.

---

## Baseline Scores

The scripted deterministic baseline (no LLM needed) scores:

| Task | Score | Seed | Scenario |
|---|---|---|---|
| Task 1 — Alert Triage | **1.00** | 42 | task1_easy_001 |
| Task 2 — Root Cause Analysis | **0.50** | 42 | task2_medium_003 |
| Task 3 — Full Incident Playbook | **0.57** | 42 | task3_hard_003 |
| **Average** | **0.69** | | |

These scores are reproducible. To verify locally:

```bash
BASELINE_PROVIDER=scripted BASELINE_ENV_URL=asgi://local python baseline.py
```

Or trigger via API (needs `BASELINE_PROVIDER=scripted` set as a Space secret):

```bash
curl -X POST https://kyoiske-incident-ops-env.hf.space/baseline
```

The scripted baseline is a deterministic rule-based agent. A GPT-4 class agent should score 0.85–1.0 on Task 1, 0.7–0.9 on Task 2, and 0.6–0.8 on Task 3.

---

## Local Setup

```bash
git clone https://github.com/Rawknee-69/incident_ops_env
cd incident_ops_env
pip install uv
uv venv .venv
uv pip install -e ".[dev]"

# Start the server
uvicorn incident_ops_env.server.app:app --port 7860 --reload

# Open dashboard
open http://localhost:7860

# Open Gradio UI
open http://localhost:7860/ui
```

With a real LLM:
```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY or GEMINI_API_KEY
BASELINE_ENV_URL=http://localhost:7860 python baseline.py
```

Without any API key (scripted agent):
```bash
BASELINE_PROVIDER=scripted BASELINE_ENV_URL=asgi://local python baseline.py
```

---

## Docker

```bash
docker build -t incident-ops-env .
docker run -p 7860:7860 incident-ops-env

# With API keys
docker run -p 7860:7860 \
  -e OPENAI_API_KEY=sk-... \
  incident-ops-env
```

---

## Run Tests

```bash
python -m pytest tests/ -v
# Expected: 14 passed
```

## Validate OpenEnv Compliance

```bash
# Local (checks pyproject, Dockerfile, server structure)
openenv validate .

# Remote (checks live API endpoints)
openenv validate https://kyoiske-incident-ops-env.hf.space
```

## Phase Readiness Check

```bash
# Full automated check of all hackathon submission requirements
python scripts/phase_readiness.py --runs 3

# Against the live Space
python scripts/phase_readiness.py --runs 3 \
  --space-url "https://kyoiske-incident-ops-env.hf.space"
```

---

## Environment Variables

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI key for baseline and agent runs |
| `OPENAI_MODEL` | `gpt-4o-mini` | Which OpenAI model to use |
| `GEMINI_API_KEY` | — | Google Gemini key (alternative to OpenAI) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Which Gemini model to use |
| `BASELINE_PROVIDER` | — | Set to `scripted` to run baseline without any API key |
| `BASELINE_ENV_URL` | `http://localhost:7860` | Where baseline.py points to. Set to `asgi://local` to skip HTTP |
| `WORKERS` | `1` | Number of uvicorn worker processes |
| `MAX_CONCURRENT_ENVS` | `100` | Max concurrent sessions per worker |
| `UI_BACKEND_URL` | `http://127.0.0.1:7860` | Backend URL for Gradio UI |
| `UI_LOG_LEVEL` | `INFO` | Gradio UI log verbosity |

---

## Project Structure

```
incident_ops_env/
├── Dockerfile                          # Docker build for HF Spaces
├── pyproject.toml                      # Dependencies and package config
├── openenv.yaml                        # OpenEnv metadata manifest
├── baseline.py                         # Scripted + LLM baseline runner
├── .env.example                        # Copy to .env and add your keys
│
├── incident_ops_env/
│   ├── models.py                       # All Pydantic models (Action, Observation, State, etc.)
│   ├── client.py                       # Python client library (async + sync)
│   │
│   ├── scenarios/                      # 10 built-in incident scenarios (JSON)
│   │   ├── task1_easy_001.json         # Alert triage: database overload
│   │   ├── task1_easy_002.json         # Alert triage: network partition
│   │   ├── task1_easy_003.json         # Alert triage: memory leak
│   │   ├── task1_payment_db_alert.json # Alert triage: payment variant
│   │   ├── task2_medium_001.json       # RCA: deployment regression
│   │   ├── task2_medium_002.json       # RCA: authentication failure
│   │   ├── task2_medium_003.json       # RCA: disk full
│   │   ├── task3_hard_001.json         # Playbook: DB pool exhaustion
│   │   ├── task3_hard_002.json         # Playbook: network degradation
│   │   └── task3_hard_003.json         # Playbook: search platform incident
│   │
│   └── server/
│       ├── app.py                      # FastAPI app — all HTTP + WebSocket endpoints
│       ├── environment.py              # Core RL environment logic (reset/step/grade)
│       ├── reward.py                   # Per-step reward function
│       ├── graders.py                  # Episode graders for all 3 tasks
│       ├── session_manager.py          # Session TTL management
│       ├── scenario_loader.py          # JSON scenario loading + validation
│       ├── scenario_registry.py        # User-uploaded scenario storage
│       ├── metrics.py                  # Thread-safe metrics collector + live hub
│       ├── llm_provider.py             # OpenAI + Gemini provider abstraction
│       ├── entrypoint.py               # CLI entrypoint for `server` script
│       └── static/index.html           # Live dashboard (auto-refreshes every 2s)
│
├── ui/
│   └── gradio_app.py                   # Gradio observability UI (mounted at /ui)
│
├── tests/
│   ├── test_api.py                     # HTTP endpoint tests
│   ├── test_environment_core.py        # Environment logic tests
│   └── test_models_and_scenarios.py    # Model + scenario validation tests
│
└── scripts/
    └── phase_readiness.py              # Pre-submission automated gate checks
```

---

## License

MIT