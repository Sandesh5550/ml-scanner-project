from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.db_session import get_db
from services.metrics import (
    get_metrics_exposition,
    record_batch_job_status,
    record_external_off_failure,
    record_redis_status,
)


def test_health_liveness_probe():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "running"}


def test_ready_probe_healthy():
    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)
    # Standard orchestrator readiness probe
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "ready", "database": "connected"}

    # Detailed structured readiness probe
    resp_detailed = client.get("/ready?detailed=true")
    assert resp_detailed.status_code == 200
    data_detailed = resp_detailed.json()
    assert data_detailed["status"] == "ready"
    assert data_detailed["database"] == "connected"
    assert "redis" in data_detailed
    assert "datasets" in data_detailed

    # Parity check on /health/ready alias
    resp_alias = client.get("/health/ready")
    assert resp_alias.status_code == 200
    assert resp_alias.json() == data

    app.dependency_overrides.clear()


def test_ready_probe_database_unavailable():
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("Database connection pool exhausted")
    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()

    app.dependency_overrides.clear()


def test_phase10_metrics_recording_and_exposition():
    # Record metrics
    record_redis_status(True)
    record_batch_job_status("completed", 1.25)
    record_external_off_failure("rate_limited")

    payload, content_type = get_metrics_exposition()
    decoded = payload.decode("utf-8")

    assert "redis_connected" in decoded
    assert "batch_jobs_total" in decoded
    assert "batch_job_duration_seconds" in decoded
    assert "external_off_failures_total" in decoded
    assert 'status="completed"' in decoded
    assert 'reason="rate_limited"' in decoded
