from fastapi.testclient import TestClient

from gpumon.api import app
from helpers import make_event


def test_ingest_and_health_shape():
    with TestClient(app) as client:
        batch = [make_event(gpu=g).model_dump() for g in range(4)]
        r = client.post("/events", json=batch)
        assert r.status_code == 200
        assert r.json()["accepted"] == 4

        h = client.get("/health").json()
        assert h["type"] == "snapshot"
        assert "jobs" in h
        assert h["events_ingested"] >= 4


def test_dashboard_served():
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "GPU anomaly monitoring" in r.text


def test_incident_not_found():
    with TestClient(app) as client:
        assert client.get("/incidents/does-not-exist").status_code == 404
