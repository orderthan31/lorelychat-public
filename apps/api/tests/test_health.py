def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_ready_probes_database_after_lifespan_startup(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "database": "ok",
        "dispatchers_started": False,
    }
