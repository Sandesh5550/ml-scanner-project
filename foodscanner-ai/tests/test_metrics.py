from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-metrics-tests"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db
from database.models import User
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.metrics import (
    METRICS_REGISTRY,
    get_metrics_exposition,
    normalize_metric_endpoint,
    record_http_request,
    record_ocr_confidence,
    record_ocr_job_created,
    record_ocr_job_status,
    record_ocr_stale_reaped,
    record_rate_limit_exceeded,
)
from services.rate_limiter import SlidingWindowRateLimiter, enforce_rate_limit
from api.main import app


def create_test_image_b64(width: int = 150, height: int = 80, color: str = "white") -> str:
    """Generate in-memory PNG as base64 string."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture(scope="module")
def client():
    orm_init_db()
    return TestClient(app)


@pytest.fixture(scope="module")
def test_user():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "metrics_test_user@example.com").first()
        if not user:
            user = User(
                email="metrics_test_user@example.com",
                name="Metrics User",
                hashed_password=hash_password("metricsPassword123"),
                daily_calorie_limit=2000,
                created_at=db_service._utc_now_str(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user_id=int(user.id))
        return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}
    finally:
        db.close()


from prometheus_client import CONTENT_TYPE_LATEST

def test_metrics_endpoint_returns_prometheus_exposition(client):
    """GET /metrics returns 200 with Prometheus text/plain content type."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    assert response.headers.get("content-type") == CONTENT_TYPE_LATEST

    content = response.text
    # Verify core Prometheus metrics are defined in exposition
    assert "# HELP http_requests_total" in content
    assert "# TYPE http_requests_total counter" in content
    assert "# HELP http_request_duration_seconds" in content
    assert "# HELP ocr_jobs_total" in content
    assert "# HELP rate_limit_exceeded_total" in content


def test_metrics_endpoint_excluded_from_http_metrics(client):
    """The /metrics route itself should not be recorded in http_requests_total to avoid self-monitoring loop."""
    # Hit /metrics twice
    client.get("/metrics")
    response = client.get("/metrics")
    assert response.status_code == 200
    content = response.text

    # Verify no line has endpoint="/metrics"
    assert 'endpoint="/metrics"' not in content


def test_http_requests_total_increments_on_requests(client):
    """Calling application endpoints increments http_requests_total and observes latency."""
    # Request /health
    health_res = client.get("/health")
    assert health_res.status_code == 200

    metrics_res = client.get("/metrics")
    assert metrics_res.status_code == 200
    content = metrics_res.text

    # Verify /health request counter
    assert 'http_requests_total{endpoint="/health",method="GET",status_code="200"}' in content
    # Verify histogram observation for /health
    assert 'http_request_duration_seconds_count{endpoint="/health",method="GET"}' in content


def test_endpoint_normalization_bounded_cardinality():
    """Path normalization prevents label explosion from IDs, barcodes, and queries."""
    # Query parameters stripped
    assert normalize_metric_endpoint("/health?query=123") == "/health"

    # Static routes
    assert normalize_metric_endpoint("/ocr") == "/ocr"
    assert normalize_metric_endpoint("/register") == "/register"

    # Dynamic route templates
    assert normalize_metric_endpoint("/product/8901030383748") == "/product/{barcode}"
    assert normalize_metric_endpoint("/ocr/jobs/ocr_a1b2c3d4e5f6") == "/ocr/jobs/{job_id}"
    assert normalize_metric_endpoint("/food-log/42") == "/food-log/{log_id}"
    assert normalize_metric_endpoint("/alternatives/890123") == "/alternatives/{barcode}"

    # Matched route template override from FastAPI
    assert normalize_metric_endpoint("/product/123", matched_route="/product/{barcode}") == "/product/{barcode}"

    # Overly long or unrecognized paths fall back to bounded /unmatched
    long_unknown = "/unknown/path/" + "x" * 50
    assert normalize_metric_endpoint(long_unknown) == "/unmatched"


def test_unmatched_route_404_recorded_safely(client):
    """404 requests to non-existent paths are recorded with bounded endpoint label."""
    res = client.get("/non-existent-metric-test-path")
    assert res.status_code == 404

    metrics_res = client.get("/metrics")
    content = metrics_res.text
    # Ensure 404 is tracked under a safe bounded label
    assert 'status_code="404"' in content


def test_rate_limit_metric_recorded_on_rejection(client):
    """When a rate limit is exceeded, rate_limit_exceeded_total is incremented with clean endpoint label."""
    strict_limiter = SlidingWindowRateLimiter()
    strict_limiter.get_limits = lambda: {"test_rl": (1, 60)}
    mock_request = type("MockReq", (), {
        "headers": {},
        "client": type("MockClient", (), {"host": "127.0.0.1"})(),
        "query_params": {},
    })()

    # First request succeeds
    enforce_rate_limit("test_rl", mock_request, limiter=strict_limiter)

    # Second request exceeds limit and triggers 429
    with pytest.raises(Exception) as exc_info:
        enforce_rate_limit("test_rl", mock_request, limiter=strict_limiter)
    assert exc_info.value.status_code == 429

    metrics_res = client.get("/metrics")
    assert 'rate_limit_exceeded_total{endpoint="test_rl"}' in metrics_res.text


def test_ocr_metric_functions():
    """Unit test individual metric recording functions against the dedicated registry."""
    record_ocr_job_created("async")
    record_ocr_job_status("completed", duration_seconds=1.23)
    record_ocr_confidence("high")
    record_ocr_stale_reaped(3)

    payload, _ = get_metrics_exposition()
    text = payload.decode("utf-8")

    assert 'ocr_jobs_total{mode="async"}' in text
    assert 'ocr_job_status_total{status="completed"}' in text
    assert 'ocr_confidence_total{level="high"}' in text
    assert "ocr_stale_jobs_reaped_total" in text
    assert "ocr_job_duration_seconds_count" in text


def test_ocr_sync_endpoint_records_metrics(client, test_user):
    """POST /ocr in sync mode records ocr_jobs_total{mode='sync'} and status."""
    img_b64 = create_test_image_b64()
    with patch("api.main.process_image_ocr") as mock_ocr:
        mock_ocr.return_value = {
            "status": "SUCCESS",
            "product_name": "Test Product",
            "calories": 100,
            "fat": 2.0,
            "sugar": 5.0,
            "salt": 0.5,
            "protein": 3.0,
            "confidence": "high",
        }
        res = client.post(
            "/ocr",
            json={"image_base64": img_b64},
            headers=test_user["headers"],
        )
        assert res.status_code == 200

    metrics_res = client.get("/metrics")
    content = metrics_res.text
    assert 'ocr_jobs_total{mode="sync"}' in content
    assert 'ocr_job_status_total{status="completed"}' in content
    assert 'ocr_confidence_total{level="high"}' in content


def test_ocr_async_endpoint_records_metrics(client, test_user):
    """POST /ocr?async=true records ocr_jobs_total{mode='async'}."""
    img_b64 = create_test_image_b64()
    with patch("api.main.execute_ocr_background_task"):
        res = client.post(
            "/ocr?async=true",
            json={"image_base64": img_b64},
            headers=test_user["headers"],
        )
        assert res.status_code == 202

    metrics_res = client.get("/metrics")
    content = metrics_res.text
    assert 'ocr_jobs_total{mode="async"}' in content


def test_metrics_initialized_with_zero_samples(client):
    """Metrics endpoint exports initialized 0.0 samples for all bounded label combinations."""
    response = client.get("/metrics")
    assert response.status_code == 200
    content = response.text

    # OCR jobs modes
    assert 'ocr_jobs_total{mode="sync"}' in content
    assert 'ocr_jobs_total{mode="async"}' in content

    # OCR job terminal statuses
    assert 'ocr_job_status_total{status="completed"}' in content
    assert 'ocr_job_status_total{status="failed"}' in content

    # OCR confidence levels
    assert 'ocr_confidence_total{level="high"}' in content
    assert 'ocr_confidence_total{level="medium"}' in content
    assert 'ocr_confidence_total{level="low"}' in content

    # Rate limiting endpoint categories
    assert 'rate_limit_exceeded_total{endpoint="auth"}' in content
    assert 'rate_limit_exceeded_total{endpoint="ocr"}' in content
    assert 'rate_limit_exceeded_total{endpoint="scan"}' in content
    assert 'rate_limit_exceeded_total{endpoint="chat"}' in content


def test_record_ocr_confidence_numeric_and_string():
    """OCR confidence recording maps both numeric and qualitative values safely."""
    record_ocr_confidence(0.95)
    record_ocr_confidence(0.50)
    record_ocr_confidence(0.20)
    record_ocr_confidence("HIGH")
    record_ocr_confidence("unknown_level")  # defaults safely to "low"

    payload, _ = get_metrics_exposition()
    text = payload.decode("utf-8")
    assert 'ocr_confidence_total{level="high"}' in text
    assert 'ocr_confidence_total{level="medium"}' in text
    assert 'ocr_confidence_total{level="low"}' in text
