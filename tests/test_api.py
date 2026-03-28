from fastapi.testclient import TestClient

from incident_ops_env.server.app import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_reset_step_state():
    client = TestClient(app)
    reset = client.post("/reset", json={"task_id": 1, "seed": 42})
    assert reset.status_code == 200
    session_id = reset.json()["session_id"]

    step = client.post(
        "/step",
        json={"action": {"action_type": "classify_alert", "severity": "P2", "service_name": "payment-service", "pattern_type": "database_overload"}},
        headers={"X-Session-ID": session_id},
    )
    assert step.status_code == 200
    assert "reward" in step.json()

    state = client.get("/state", headers={"X-Session-ID": session_id})
    assert state.status_code == 200
    assert state.json()["episode_id"]


def test_baseline_returns_503_without_provider_key(monkeypatch):
    import baseline

    async def _raise(**_kwargs):
        raise EnvironmentError("missing")

    monkeypatch.setattr(baseline, "run_baseline", _raise)
    client = TestClient(app)
    response = client.post("/baseline")
    assert response.status_code == 503
    assert response.json()["detail"] == "No LLM API key configured."


def test_scenarios_endpoints():
    client = TestClient(app)
    listing = client.get("/scenarios")
    assert listing.status_code == 200
    data = listing.json()
    assert "built_in" in data
    assert "task_1" in data["built_in"]

    valid_payload = {
        "scenario": {
            "task_id": 1,
            "scenario_id": "uploaded_task1_sample",
            "alerts": [
                {
                    "alert_id": "ALT-UP-1",
                    "title": "uploaded alert",
                    "severity": "P2",
                    "service": "payment-service",
                    "triggered_at": "2026-03-27T01:00:00Z",
                    "metadata": {"resolved": False},
                }
            ],
            "ground_truth": {"severity": "P2", "service": "payment-service", "pattern_type": "database_overload"},
        }
    }
    validate = client.post("/scenarios/validate", json=valid_payload)
    assert validate.status_code == 200
    upload = client.post("/scenarios/upload", json=valid_payload)
    assert upload.status_code == 200


def test_metrics_websocket_stream_snapshot():
    client = TestClient(app)
    with client.websocket_connect("/ws/metrics") as websocket:
        message = websocket.receive_json()
        assert message["type"] in {"snapshot", "request", "episode_start", "step", "episode_end", "grader"}
