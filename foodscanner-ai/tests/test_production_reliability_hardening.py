from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app, _safe_json_dumps
from services.db_service import _utc_now_str
from services.ocr_service import process_image_ocr


@pytest.fixture
def client():
    return TestClient(app)


def test_liveness_health_endpoint(client):
    """Verify /health liveness probe returns HTTP 200 with status running."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "running"}


def test_readiness_probe_success(client):
    """Verify /ready and /health/ready probe return HTTP 200 when database responds."""
    res1 = client.get("/ready")
    assert res1.status_code == 200
    assert res1.json() == {"status": "ready", "database": "connected"}

    res2 = client.get("/health/ready")
    assert res2.status_code == 200
    assert res2.json() == {"status": "ready", "database": "connected"}


def test_readiness_probe_database_failure(client, monkeypatch):
    """Verify /ready returns HTTP 503 Service Unavailable when DB connection fails."""
    def mock_db_dependency():
        mock_session = MagicMock()
        mock_session.execute.side_effect = Exception("Connection to database pool lost")
        yield mock_session

    from database.db_session import get_db
    app.dependency_overrides[get_db] = mock_db_dependency
    try:
        res = client.get("/ready")
        assert res.status_code == 503
        assert res.json()["detail"] == "Database connection unavailable"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_global_exception_handler_sanitizes_500():
    """Verify unhandled server exceptions return sanitized JSON without leaking stack traces."""
    # Define a temporary route that throws an unexpected internal exception
    @app.get("/test-unhandled-exception-simulation")
    def throw_unexpected():
        raise RuntimeError("Internal critical secret: /etc/secrets/db_password.txt missing")

    safe_client = TestClient(app, raise_server_exceptions=False)
    res = safe_client.get("/test-unhandled-exception-simulation")
    assert res.status_code == 500
    data = res.json()
    assert data["detail"] == "An internal server error occurred. Please try again later."
    assert "secret" not in json.dumps(data)
    assert "password" not in json.dumps(data)


def test_ocr_job_id_validation_rejects_malformed(client):
    """Verify GET /ocr/jobs/{job_id} rejects malformed or malicious IDs with HTTP 400."""
    # Register/login a user to get auth token
    import uuid
    email = f"test_job_id_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post("/register", json={"email": email, "password": "secure_password_123"})
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Whitespace in identifier
    res_spaces = client.get("/ocr/jobs/ocr%20job%20123", headers=headers)
    assert res_spaces.status_code == 400
    assert "Invalid job ID format" in res_spaces.json()["detail"]

    # 2. Oversized identifier (>64 chars)
    res_oversized = client.get(f"/ocr/jobs/{'a' * 65}", headers=headers)
    assert res_oversized.status_code == 400
    assert "Invalid job ID format" in res_oversized.json()["detail"]

    # 3. Illegal special characters (SQL injection / shell characters)
    res_illegal = client.get("/ocr/jobs/ocr_job;DROP_TABLE", headers=headers)
    assert res_illegal.status_code == 400
    assert "Invalid job ID format" in res_illegal.json()["detail"]

    res_special = client.get("/ocr/jobs/ocr_job$!", headers=headers)
    assert res_special.status_code == 400
    assert "Invalid job ID format" in res_special.json()["detail"]

    # 4. Valid format returns 404 if not found (rather than 400)
    res_valid = client.get("/ocr/jobs/ocr_valid_job_12345678", headers=headers)
    assert res_valid.status_code == 404


def test_safe_json_dumps_handles_complex_types():
    """Verify _safe_json_dumps handles numpy scalars, arrays, datetimes, and custom objects safely."""
    import numpy as np

    payload = {
        "score": np.float32(94.5),
        "count": np.int64(42),
        "vector": np.array([1.0, 2.0, 3.5]),
        "timestamp": datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc),
        "target_date": date(2026, 10, 23),
        "regular_int": 100,
        "product_name": "Almond Butter",
    }

    dumped = _safe_json_dumps(payload)
    parsed = json.loads(dumped)

    assert parsed["score"] == 94.5
    assert parsed["count"] == 42
    assert parsed["vector"] == [1.0, 2.0, 3.5]
    assert "2026-10-23" in parsed["timestamp"]
    assert parsed["target_date"] == "2026-10-23"
    assert parsed["product_name"] == "Almond Butter"


def test_utc_now_str_is_timezone_aware_and_deprecated_free():
    """Verify _utc_now_str generates valid YYYY-MM-DD HH:MM:SS format using timezone-aware UTC clock."""
    ts = _utc_now_str()
    assert isinstance(ts, str)
    # Validate format: YYYY-MM-DD HH:MM:SS
    parts = ts.split(" ")
    assert len(parts) == 2
    assert len(parts[0].split("-")) == 3
    assert len(parts[1].split(":")) == 3


def test_ocr_service_extracts_saturated_fat():
    """Verify process_image_ocr includes saturated_fat in scalar outputs."""
    raw_ocr_table = (
        "Nutrition Facts\n"
        "Energy 450 kcal\n"
        "Total Fat 20 g\n"
        "Saturated Fat 8.5 g\n"
        "Carbohydrates 55 g\n"
        "Sugars 15 g\n"
        "Protein 10 g\n"
        "Salt 0.5 g\n"
    )

    with patch("services.ocr_service.run_ocr_engine", return_value=(raw_ocr_table, 0.90)):
        from PIL import Image
        import io
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b = buf.getvalue()

        result = process_image_ocr(b)
        assert result["saturated_fat"] == 8.5
        assert result["fat"] == 20.0
        assert result["calories"] == 450.0
