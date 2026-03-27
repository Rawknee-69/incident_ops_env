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

    monkeypatch.setattr(baseline, "run_baseline_sync", lambda: (_ for _ in ()).throw(EnvironmentError("missing")))
    client = TestClient(app)
    response = client.post("/baseline")
    assert response.status_code == 503
    assert response.json()["detail"] == "No LLM API key configured."
