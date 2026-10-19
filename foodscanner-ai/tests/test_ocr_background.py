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

from datetime import datetime, timezone, timedelta
from database.orm import SessionLocal, init_db as orm_init_db
from database.models import User, OCRJob
from services import db_service
from services.auth_service import hash_password, create_access_token
from api.main import app, execute_ocr_background_task, recover_stale_ocr_jobs, get_ocr_job_timeout_seconds


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


def test_stale_processing_job_recovered_on_poll(client, user_a):
    """Verify that a job stranded in 'processing' beyond timeout is marked failed on status poll."""
    job_id = f"ocr_stale_proc_{int(datetime.now().timestamp())}"
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=user_a["id"],
            status="processing",
            created_at=stale_time,
            started_at=stale_time,
            completed_at=None,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Query status endpoint: JIT recovery should detect timeout and transition to failed
    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "failed"
    assert "timed out" in data["error"].lower() or "restarted" in data["error"].lower()
    assert data["completed_at"] is not None
    assert data["result"] is None


def test_stale_pending_job_recovered_on_poll(client, user_a):
    """Verify that a job stranded in 'pending' beyond timeout is marked failed on status poll."""
    job_id = f"ocr_stale_pend_{int(datetime.now().timestamp())}"
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=user_a["id"],
            status="pending",
            created_at=stale_time,
            started_at=None,
            completed_at=None,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "failed"
    assert "timed out" in data["error"].lower()
    assert data["completed_at"] is not None


def test_fresh_processing_job_not_marked_failed(client, user_a):
    """Verify that a legitimate running job well within timeout is NOT marked failed."""
    job_id = f"ocr_fresh_{int(datetime.now().timestamp())}"
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=user_a["id"],
            status="processing",
            created_at=fresh_time,
            started_at=fresh_time,
            completed_at=None,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "processing"
    assert data["error"] is None
    assert data["completed_at"] is None


def test_completed_job_never_overwritten_by_stale_recovery(client, user_a):
    """Verify that completed jobs are never touched by stale recovery, even if very old."""
    job_id = f"ocr_comp_{int(datetime.now().timestamp())}"
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=user_a["id"],
            status="completed",
            created_at=old_time,
            started_at=old_time,
            completed_at=old_time,
            result_json=json.dumps({"calories": 250.0, "product_name": "Historical Oats"}),
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Trigger recovery explicitly
    db = SessionLocal()
    try:
        reaped = recover_stale_ocr_jobs(db, job_id=job_id)
        assert reaped == 0
    finally:
        db.close()

    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    data = poll_res.json()
    assert data["status"] == "completed"
    assert data["result"]["calories"] == 250.0
    assert data["error"] is None


def test_configurable_timeout_environment_variable(client, user_a):
    """Verify OCR_JOB_TIMEOUT_SECONDS controls timeout threshold."""
    job_id = f"ocr_cfg_timeout_{int(datetime.now().timestamp())}"
    job_time = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=user_a["id"],
            status="processing",
            created_at=job_time,
            started_at=job_time,
            completed_at=None,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Under default 120s timeout, 15s job is fresh
    with patch.dict(os.environ, {"OCR_JOB_TIMEOUT_SECONDS": "120"}):
        assert get_ocr_job_timeout_seconds() == 120
        db = SessionLocal()
        try:
            reaped = recover_stale_ocr_jobs(db, job_id=job_id)
            assert reaped == 0
        finally:
            db.close()

    # Under overridden 10s timeout, 15s job is stale
    with patch.dict(os.environ, {"OCR_JOB_TIMEOUT_SECONDS": "10"}):
        assert get_ocr_job_timeout_seconds() == 10
        db = SessionLocal()
        try:
            reaped = recover_stale_ocr_jobs(db, job_id=job_id)
            assert reaped == 1
        finally:
            db.close()

    poll_res = client.get(f"/ocr/jobs/{job_id}", headers=user_a["headers"])
    assert poll_res.status_code == 200
    assert poll_res.json()["status"] == "failed"


def test_worker_skips_writing_if_job_transitioned_out_of_processing():
    """Verify that if a job was already reaped/failed, worker does not clobber it with completed."""
    job_id = f"ocr_race_{int(datetime.now().timestamp())}"
    now_str = datetime.now(timezone.utc).isoformat()
    db = SessionLocal()
    try:
        job = OCRJob(
            id=job_id,
            user_id=1,
            status="failed",
            created_at=now_str,
            started_at=now_str,
            completed_at=now_str,
            error_message="OCR processing timed out or worker restarted. Please retry your scan.",
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Attempt to execute task on already-failed job
    tiny_img_bytes = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(tiny_img_bytes, format="PNG")
    execute_ocr_background_task(job_id=job_id, image_bytes=tiny_img_bytes.getvalue())

    # Verify status remained failed and was not clobbered
    db = SessionLocal()
    try:
        verified_job = db.query(OCRJob).filter(OCRJob.id == job_id).first()
        assert verified_job.status == "failed"
        assert verified_job.result_json is None
    finally:
        db.close()


def test_startup_sweep_recovers_stale_jobs(user_a):
    """Verify startup sweep reaps all stale jobs across the entire database."""
    job_id_1 = f"ocr_sweep_1_{int(datetime.now().timestamp())}"
    job_id_2 = f"ocr_sweep_2_{int(datetime.now().timestamp())}"
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()

    db = SessionLocal()
    try:
        j1 = OCRJob(id=job_id_1, user_id=user_a["id"], status="processing", created_at=stale_time, started_at=stale_time)
        j2 = OCRJob(id=job_id_2, user_id=user_a["id"], status="processing", created_at=fresh_time, started_at=fresh_time)
        db.add_all([j1, j2])
        db.commit()

        # Run global recovery sweep (job_id=None)
        reaped = recover_stale_ocr_jobs(db, job_id=None, timeout_seconds=120)
        assert reaped >= 1

        db.refresh(j1)
        db.refresh(j2)
        assert j1.status == "failed"
        assert j2.status == "processing"
    finally:
        db.close()
