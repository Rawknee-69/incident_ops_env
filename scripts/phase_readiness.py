from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass

from fastapi.testclient import TestClient

from baseline import run_baseline_sync
from incident_ops_env.server.app import app
from incident_ops_env.server.scenario_loader import list_scenarios_for_task


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_openenv_contract() -> CheckResult:
    client = TestClient(app)
    required = ["/health", "/tasks", "/reset", "/step", "/state", "/grader"]
    found = {route.path for route in app.routes}
    missing = [path for path in required if path not in found]
    if missing:
        return CheckResult("OpenEnv endpoint contract", False, f"Missing endpoints: {missing}")

    health = client.get("/health")
    tasks = client.get("/tasks")
    if health.status_code != 200 or tasks.status_code != 200:
        return CheckResult("OpenEnv endpoint contract", False, "Health/tasks endpoints not responding with 200")
    return CheckResult("OpenEnv endpoint contract", True, "Required endpoints are present and reachable")


def check_tasks_and_graders() -> CheckResult:
    counts = {task_id: len(list_scenarios_for_task(task_id)) for task_id in (1, 2, 3)}
    if any(v < 1 for v in counts.values()):
        return CheckResult("3+ tasks with graders", False, f"Missing scenarios for tasks: {counts}")
    if len(counts) < 3:
        return CheckResult("3+ tasks with graders", False, "Expected at least 3 tasks")
    return CheckResult("3+ tasks with graders", True, f"Task scenarios present: {counts}")


def check_phase1_api_flow() -> CheckResult:
    client = TestClient(app)
    reset = client.post("/reset", json={"task_id": 1, "seed": 42})
    if reset.status_code != 200:
        return CheckResult("Baseline API flow", False, f"/reset failed with {reset.status_code}")
    session_id = reset.json()["session_id"]
    step = client.post(
        "/step",
        json={"action": {"action_type": "classify_alert", "severity": "P2", "service_name": "payment-service", "pattern_type": "database_overload"}},
        headers={"X-Session-ID": session_id},
    )
    if step.status_code != 200:
        return CheckResult("Baseline API flow", False, f"/step failed with {step.status_code}")
    grade = client.post("/grader", json={"session_id": session_id})
    if grade.status_code != 200:
        return CheckResult("Baseline API flow", False, f"/grader failed with {grade.status_code}")
    return CheckResult("Baseline API flow", True, "reset -> step -> grader path works")


def check_baseline_reproducibility(runs: int) -> CheckResult:
    original_provider = os.environ.get("BASELINE_PROVIDER")
    original_url = os.environ.get("BASELINE_ENV_URL")
    os.environ["BASELINE_PROVIDER"] = "scripted"
    os.environ["BASELINE_ENV_URL"] = "asgi://local"
    try:
        results = [run_baseline_sync() for _ in range(runs)]
    finally:
        if original_provider is None:
            os.environ.pop("BASELINE_PROVIDER", None)
        else:
            os.environ["BASELINE_PROVIDER"] = original_provider
        if original_url is None:
            os.environ.pop("BASELINE_ENV_URL", None)
        else:
            os.environ["BASELINE_ENV_URL"] = original_url

    avgs = [r["average_score"] for r in results]
    unique = sorted(set(avgs))
    passed = len(unique) == 1
    return CheckResult(
        "Baseline reproduces",
        passed,
        f"Average scores across {runs} scripted runs: {avgs}",
    )


def check_docker_build() -> CheckResult:
    command = ["docker", "build", "-f", "incident_ops_env/server/Dockerfile", "-t", "incident-ops-env:readiness", "."]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode == 0:
        return CheckResult("Dockerfile builds", True, "docker build succeeded")
    text = (proc.stderr or proc.stdout).strip()
    if "permission denied" in text.lower() and "docker.sock" in text.lower():
        return CheckResult("Dockerfile builds", False, "Blocked by Docker socket permissions on this machine")
    return CheckResult("Dockerfile builds", False, text.splitlines()[-1] if text else "docker build failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase readiness checks for IncidentOpsEnv.")
    parser.add_argument("--runs", type=int, default=3, help="Number of baseline runs for variance check.")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker build gate check.")
    args = parser.parse_args()

    checks = [
        check_openenv_contract(),
        check_tasks_and_graders(),
        check_phase1_api_flow(),
        check_baseline_reproducibility(runs=args.runs),
    ]
    if not args.skip_docker:
        checks.append(check_docker_build())

    print("=== Phase 1: Automated Validation ===")
    for check in checks:
        state = "PASS" if check.passed else "FAIL"
        print(f"[{state}] {check.name}: {check.detail}")

    phase1_pass = all(c.passed for c in checks)
    print(f"Phase 1 gate: {'PASS' if phase1_pass else 'FAIL'}")

    print("\n=== Phase 2: Agentic Evaluation Readiness ===")
    print("- Scripted baseline reproducibility check included above.")
    print("- Run LLM baseline with provider keys set, e.g. OPENAI_API_KEY or GEMINI_API_KEY.")
    print("- For Nemotron/Open LLM runs, point your runner at /reset,/step,/grader and log per-task scores.")
    print("- Variance check recommendation: >=3 runs/model, compare mean and stddev.")

    print("\n=== Phase 3: Human Review Readiness ===")
    print("- Include README + docs/USAGE_AND_WORKING.md for setup clarity.")
    print("- Keep deterministic seeds and scenario ids in reports.")
    print("- Keep exploit notes: runbook leaks and action-schema edge cases should be disclosed.")

    print("\nMachine-readable:")
    print(json.dumps({"phase1_pass": phase1_pass, "checks": [c.__dict__ for c in checks]}, indent=2))


if __name__ == "__main__":
    main()
