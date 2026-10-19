from __future__ import annotations

import base64
import io
import json
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

os.environ["SECRET_KEY"] = "test-secret-key-for-ocr-background-tests"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db
from database.models import User, OCRJob
from services import db_service
from services.auth_service import hash_password, create_access_token
from api.main import app, execute_ocr_background_task


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
def user_a():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "user_a_ocr@example.com").first()
        if not user:
            user = User(
                email="user_a_ocr@example.com",
                name="User A",
                hashed_password=hash_password("passwordA123"),
                daily_calorie_limit=2000,
                created_at=db_service._utc_now_str(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user_id=int(user.id))
        return {"id": user.id, "token": token, "headers": {"Authorization": f"Bearer {token}"}}
    finally:
        db.close()


@pytest.fixture(scope="module")
def user_b():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "user_b_ocr@example.com").first()
        if not user:
            user = User(
                email="user_b_ocr@example.com",
                name="User B",
                hashed_password=hash_password("passwordB123"),
                daily_calorie_limit=2000,
                created_at=db_service._utc_now_str(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user_id=int(user.id))
        return {"id": user.id, "token": token, "headers": {"Authorization": f"Bearer {token}"}}
    finally:
        db.close()


def test_sync_ocr_backward_compatibility(client, user_a):
    """Verify POST /ocr default behavior is synchronous and returns HTTP 200 with full nutrition data."""
    img_b64 = create_test_image_b64()
    res = client.post("/ocr", json={"image_base64": img_b64}, headers=user_a["headers"])
    assert res.status_code == 200

    data = res.json()
    # Required nutrition fields for existing mobile app
    assert "calories" in data
    assert "fat" in data
    assert "sugar" in data
    assert "salt" in data
    assert "protein" in data
    assert "fiber" in data
    assert "carbs" in data
    assert "product_name" in data
    assert "confidence" in data
    assert "raw_text" in data
    assert "ocr_version" in data


def test_async_ocr_submission_via_query_param(client, user_a):
    """Verify POST /ocr?async=true returns HTTP 202 Accepted with job_id and poll_url."""
    img_b64 = create_test_image_b64()
    res = client.post("/ocr?async=true", json={"image_base64": img_b64}, headers=user_a["headers"])
    assert res.status_code == 202

    data = res.json()
    assert "job_id" in data
    assert data["job_id"].startswith("ocr_")
    assert data["status"] in ("pending", "processing", "completed")
    assert "created_at" in data
    assert data["poll_url"] == f"/ocr/jobs/{data['job_id']}"

    # Poll status endpoint
    poll_res = client.get(data["poll_url"], headers=user_a["headers"])
    assert poll_res.status_code == 200
    poll_data = poll_res.json()
    assert poll_data["job_id"] == data["job_id"]
    assert poll_data["status"] == "completed"
    assert poll_data["result"] is not None
    assert "calories" in poll_data["result"]
    assert poll_data["error"] is None


def test_async_ocr_submission_via_body_flag(client, user_a):
    """Verify POST /ocr with async_mode=True in JSON body returns HTTP 202."""
    img_b64 = create_test_image_b64()
    res = client.post("/ocr", json={"image_base64": img_b64, "async_mode": True}, headers=user_a["headers"])
    assert res.status_code == 202

    data = res.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "processing", "completed")


def test_async_ocr_invalid_payload_fails_fast_without_creating_job(client, user_a):
    """Verify invalid image payloads fail immediately with 400 and create no orphaned job."""
    db = SessionLocal()
    initial_job_count = db.query(OCRJob).count()
    db.close()

    # Empty base64
    res_empty = client.post("/ocr?async=true", json={"image_base64": ""}, headers=user_a["headers"])
    assert res_empty.status_code == 400

    # Invalid base64
    res_inv = client.post("/ocr?async=true", json={"image_base64": "invalid_base64!!!"}, headers=user_a["headers"])
    assert res_inv.status_code == 400

    # Corrupted image bytes (valid base64, but not an image)
    corrupted_b64 = base64.b64encode(b"not an image at all").decode("ascii")
    res_corrupt = client.post("/ocr?async=true", json={"image_base64": corrupted_b64}, headers=user_a["headers"])
    assert res_corrupt.status_code == 400

    # Verify no new jobs were persisted
    db = SessionLocal()
    final_job_count = db.query(OCRJob).count()
    db.close()
    assert final_job_count == initial_job_count


def test_async_ocr_oversized_payload_fails_fast_with_413(client, user_a):
    """Verify oversized payloads (> 16MB) fail fast with 413 without creating a job."""
    db = SessionLocal()
    initial_job_count = db.query(OCRJob).count()
    db.close()

    oversized_str = "A" * (16 * 1024 * 1024 + 10)
    res = client.post("/ocr?async=true", json={"image_base64": oversized_str}, headers=user_a["headers"])
    assert res.status_code == 413

    db = SessionLocal()
    final_job_count = db.query(OCRJob).count()
    db.close()
    assert final_job_count == initial_job_count


def test_ocr_job_status_requires_auth(client):
    """Verify GET /ocr/jobs/{job_id} requires JWT authentication."""
    res = client.get("/ocr/jobs/ocr_random_id")
    assert res.status_code == 401


def test_ocr_job_status_not_found(client, user_a):
    """Verify GET /ocr/jobs/{job_id} returns 404 for non-existent job."""
    res = client.get("/ocr/jobs/ocr_nonexistent_12345", headers=user_a["headers"])
    assert res.status_code == 404
    assert res.json()["detail"] == "OCR job not found"


def test_ocr_job_status_idor_protection(client, user_a, user_b):
    """Verify User B cannot access User A's OCR job (returns 404 to prevent enumeration)."""
    img_b64 = create_test_image_b64()
    submit_res = client.post("/ocr?async=true", json={"image_base64": img_b64}, headers=user_a["headers"])
    assert submit_res.status_code == 202
    job_id = submit_res.json()["job_id"]

    # User A can view the job
    owner_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert owner_res.status_code == 200

    # User B receives 404 Not Found
    other_res = client.get(f"/ocr/jobs/{job_id}", headers=user_b["headers"])
    assert other_res.status_code == 404
    assert other_res.json()["detail"] == "OCR job not found"


def test_ocr_job_failure_state_handling(client, user_a):
    """Verify that an exception during background processing sets status='failed' with safe error message."""
    img_b64 = create_test_image_b64()

    # Create job with mocked exception in process_image_ocr
    with patch("api.main.process_image_ocr", side_effect=RuntimeError("OCR engine crashed")):
        res = client.post("/ocr?async=true", json={"image_base64": img_b64}, headers=user_a["headers"])
        assert res.status_code == 202
        job_id = res.json()["job_id"]

    # In TestClient, BackgroundTasks run during request handling
    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "failed"
    assert data["error"] == "OCR processing failed"
    assert data["result"] is None
    assert data["completed_at"] is not None


def test_async_ocr_with_claims_verification(client, user_a):
    """Verify that claims passed to async OCR are verified in background task."""
    img_b64 = create_test_image_b64()
    payload = {
        "image_base64": img_b64,
        "claims": ["Zero Sugar", "High Protein"],
        "async_mode": True,
    }
    res = client.post("/ocr", json=payload, headers=user_a["headers"])
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "completed"
    assert "claim_verification" in data["result"]
