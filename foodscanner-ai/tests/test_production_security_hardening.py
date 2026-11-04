"""Tests for Production Security Hardening (Batch 15):
1. API Rate Limiting (SlidingWindowRateLimiter, per-endpoint limits, 429 status code, Retry-After header)
2. Image Payload Validation (Magic bytes, format detection, 5MB limit, corruption detection)
3. Production Default User Safeguards (ENVIRONMENT=production, SKIP_DEFAULT_USER=true)
4. Endpoint Rate Limit & Payload Integration (/ocr, /scan, /chat)
"""

from __future__ import annotations

import base64
import io
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from api.main import app
from database.init_db import ensure_default_user, should_create_default_user
from database.orm import Base
import database.models
from services.auth_service import create_access_token
from services.ocr_service import (
    MAX_IMAGE_SIZE_BYTES,
    detect_image_format,
    validate_image_payload,
)
from services.rate_limiter import (
    SlidingWindowRateLimiter,
    enforce_rate_limit,
    get_client_key,
    get_rate_limiter,
)


# ==============================================================================
# Helper to create valid synthetic images
# ==============================================================================
def create_test_image_bytes(fmt: str = "PNG", size: tuple[int, int] = (64, 64)) -> bytes:
    """Create a minimal valid image in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color="green")
    img.save(buf, format=fmt)
    return buf.getvalue()


# ==============================================================================
# 1. Rate Limiter Unit Tests
# ==============================================================================
class TestSlidingWindowRateLimiter:
    def test_basic_allow_and_sliding_window(self):
        limiter = SlidingWindowRateLimiter()
        # Set test limits: max 3 requests per 10-second window
        endpoint = "test_ep"
        client = "user:1"

        class CustomLimiter(SlidingWindowRateLimiter):
            def get_limits(self):
                return {endpoint: (3, 10)}

        custom_limiter = CustomLimiter()

        t0 = 1000.0
        # First 3 requests at t0 must succeed
        allowed, retry = custom_limiter.check_rate_limit(endpoint, client, current_time=t0)
        assert allowed is True
        assert retry == 0

        allowed, retry = custom_limiter.check_rate_limit(endpoint, client, current_time=t0 + 1)
        assert allowed is True

        allowed, retry = custom_limiter.check_rate_limit(endpoint, client, current_time=t0 + 2)
        assert allowed is True

        # 4th request within window must be blocked
        allowed, retry = custom_limiter.check_rate_limit(endpoint, client, current_time=t0 + 3)
        assert allowed is False
        assert retry > 0

        # After window slides past earliest timestamp (t0 + 10.1s), request should be allowed again
        allowed, retry = custom_limiter.check_rate_limit(endpoint, client, current_time=t0 + 10.1)
        assert allowed is True
        assert retry == 0

    def test_endpoint_isolation(self):
        """Exhausting limit on 'ocr' must not block 'scan' or 'chat'."""
        limiter = get_rate_limiter()
        limiter.reset()

        client = "user:42"
        # Exhaust OCR limit (5)
        for _ in range(5):
            allowed, _ = limiter.check_rate_limit("ocr", client)
            assert allowed is True

        # 6th OCR request blocked
        allowed, _ = limiter.check_rate_limit("ocr", client)
        assert allowed is False

        # Scan and Chat must still be allowed
        allowed_scan, _ = limiter.check_rate_limit("scan", client)
        assert allowed_scan is True

        allowed_chat, _ = limiter.check_rate_limit("chat", client)
        assert allowed_chat is True

    def test_client_key_resolution(self, monkeypatch):
        # Authenticated user
        mock_req = MagicMock()
        mock_req.headers = {}
        assert get_client_key(mock_req, user_id=10) == "user:10"

        # Anonymous with X-Forwarded-For (when trust proxy enabled)
        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        mock_req_xf = MagicMock()
        mock_req_xf.headers = {"x-forwarded-for": "203.0.113.195, 70.41.3.18"}
        assert get_client_key(mock_req_xf, user_id=None) == "ip:203.0.113.195"

        # Anonymous with X-Forwarded-For when trust proxy is disabled (prevents IP spoofing)
        monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
        mock_req_xf.client.host = "198.51.100.2"
        assert get_client_key(mock_req_xf, user_id=None) == "ip:198.51.100.2"

        # Anonymous direct client host
        mock_req_direct = MagicMock()
        mock_req_direct.headers = {}
        mock_req_direct.client.host = "192.168.1.50"
        assert get_client_key(mock_req_direct, user_id=None) == "ip:192.168.1.50"

    def test_rate_limiter_disabled_switch(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        limiter = SlidingWindowRateLimiter()
        assert limiter.is_enabled() is False

        # Should never block even if called 100 times
        for _ in range(100):
            allowed, retry = limiter.check_rate_limit("ocr", "user:99")
            assert allowed is True
            assert retry == 0


# ==============================================================================
# 2. Image Payload Validation Tests
# ==============================================================================
class TestImagePayloadValidation:
    def test_detect_image_format_magic_bytes(self):
        png_bytes = create_test_image_bytes("PNG")
        assert detect_image_format(png_bytes) == "PNG"

        jpeg_bytes = create_test_image_bytes("JPEG")
        assert detect_image_format(jpeg_bytes) == "JPEG"

        gif_bytes = create_test_image_bytes("GIF")
        assert detect_image_format(gif_bytes) == "GIF"

        bmp_bytes = create_test_image_bytes("BMP")
        assert detect_image_format(bmp_bytes) == "BMP"

        # Unknown / arbitrary bytes
        assert detect_image_format(b"RANDOM_NON_IMAGE_DATA_12345") is None
        assert detect_image_format(b"") is None

    def test_validate_image_payload_success(self):
        png_bytes = create_test_image_bytes("PNG")
        is_valid, status, err, fmt = validate_image_payload(png_bytes)
        assert is_valid is True
        assert status == 200
        assert err == ""
        assert fmt == "PNG"

    def test_validate_image_payload_oversized(self):
        # Create a payload exceeding max_bytes limit
        fake_large_payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
        is_valid, status, err, fmt = validate_image_payload(fake_large_payload, max_bytes=500)
        assert is_valid is False
        assert status == 413
        assert "exceeds maximum allowed size" in err

    def test_validate_image_payload_unsupported_format(self):
        text_payload = b"Hello, this is not an image at all."
        is_valid, status, err, fmt = validate_image_payload(text_payload)
        assert is_valid is False
        assert status == 400
        assert "Unsupported image format" in err

    def test_validate_image_payload_corrupted_structure(self):
        # Valid PNG magic header followed by corrupt garbage
        corrupt_png = b"\x89PNG\r\n\x1a\n" + b"\xff\xff\xff\xffcorrupt"
        is_valid, status, err, fmt = validate_image_payload(corrupt_png)
        assert is_valid is False
        assert status == 400
        assert "Corrupted or invalid image data" in err


# ==============================================================================
# 3. Production Default User Security Safeguards
# ==============================================================================
class TestProductionDefaultUserSecurity:
    def test_should_create_default_user_dev_mode(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("SKIP_DEFAULT_USER", raising=False)
        assert should_create_default_user() is True

    def test_should_create_default_user_production(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert should_create_default_user() is False

        monkeypatch.setenv("ENVIRONMENT", "prod")
        assert should_create_default_user() is False

    def test_should_create_default_user_skip_flag(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("SKIP_DEFAULT_USER", "true")
        assert should_create_default_user() is False

        monkeypatch.setenv("SKIP_DEFAULT_USER", "1")
        assert should_create_default_user() is False

    def test_ensure_default_user_bypasses_in_production(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ENVIRONMENT", "production")
        test_db = tmp_path / "prod_test.db"
        engine = create_engine(f"sqlite:///{test_db.as_posix()}")
        Base.metadata.create_all(engine)

        ensure_default_user(engine)

        # Confirm user 1 was NOT created
        with engine.connect() as conn:
            from sqlalchemy import text
            count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            assert count == 0


# ==============================================================================
# 4. FastAPI Endpoint Integration Tests (Rate Limiting & Payload Limits)
# ==============================================================================
class TestFastAPIHardeningEndpoints:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def auth_headers(self):
        from database.orm import SessionLocal
        from database.models import User
        uid = f"sec_test_{int(time.time() * 1000)}"
        with SessionLocal() as db:
            user = User(
                email=f"{uid}@pramaan.ai",
                hashed_password="pw_hash",
                name="Security Test User",
                daily_calorie_limit=2000,
                created_at="2026-09-29 12:00:00",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            user_id = user.id

        token = create_access_token(user_id=user_id)
        return {"Authorization": f"Bearer {token}"}

    def test_ocr_payload_validation_rejects_empty(self, client, auth_headers):
        resp = client.post("/ocr", json={"image_base64": ""}, headers=auth_headers)
        assert resp.status_code == 400
        assert "image_base64 is required" in resp.json()["detail"]

    def test_ocr_payload_validation_rejects_corrupted_data(self, client, auth_headers):
        corrupt_b64 = base64.b64encode(b"not_an_image").decode("utf-8")
        resp = client.post("/ocr", json={"image_base64": corrupt_b64}, headers=auth_headers)
        assert resp.status_code == 400
        assert "Unsupported image format" in resp.json()["detail"]

    def test_ocr_rate_limit_enforced(self, client, auth_headers):
        # Valid PNG base64
        valid_png = create_test_image_bytes("PNG")
        b64_str = base64.b64encode(valid_png).decode("utf-8")

        limiter = get_rate_limiter()
        limiter.reset()

        # Configured OCR limit is 5 per window
        for i in range(5):
            resp = client.post("/ocr", json={"image_base64": b64_str}, headers=auth_headers)
            assert resp.status_code in (200, 422), f"Call {i+1} failed with {resp.status_code}"

        # 6th call must return 429
        resp = client.post("/ocr", json={"image_base64": b64_str}, headers=auth_headers)
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    def test_ocr_payload_validation_rejects_oversized_base64(self, client, auth_headers):
        from services.ocr_service import MAX_BASE64_CHAR_LIMIT
        oversized_str = "A" * (MAX_BASE64_CHAR_LIMIT + 10)
        resp = client.post("/ocr", json={"image_base64": oversized_str}, headers=auth_headers)
        assert resp.status_code == 413
        assert "exceeds maximum allowed size" in resp.json()["detail"]

    def test_scan_rate_limit_enforced(self, client, auth_headers):
        limiter = get_rate_limiter()
        limiter.reset()

        # Configured Scan limit is 30
        for i in range(30):
            resp = client.post(
                "/scan",
                json={"barcode": "8901058851304"},
                headers=auth_headers,
            )
            # Scan might succeed or return 200
            assert resp.status_code in (200, 404)

        # 31st call must return 429
        resp = client.post(
            "/scan",
            json={"barcode": "8901058851304"},
            headers=auth_headers,
        )
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    def test_chat_rate_limit_enforced(self, client, auth_headers):
        limiter = get_rate_limiter()
        limiter.reset()

        # Configured Chat limit is 15
        for i in range(15):
            resp = client.post(
                "/chat",
                json={"message": f"Hello {i}"},
                headers=auth_headers,
            )
            assert resp.status_code == 200

        # 16th call must return 429
        resp = client.post(
            "/chat",
            json={"message": "Exceeded attempt"},
            headers=auth_headers,
        )
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    def test_login_rate_limit_enforced(self, client):
        limiter = get_rate_limiter()
        limiter.reset()

        # Configured auth limit is 5 attempts per window
        for i in range(5):
            resp = client.post(
                "/login",
                json={"email": f"ratelimit_login_{i}@example.com", "password": "WrongPassword123!"},
            )
            assert resp.status_code == 401

        # 6th attempt must return 429
        resp = client.post(
            "/login",
            json={"email": "ratelimit_login_exceeded@example.com", "password": "WrongPassword123!"},
        )
        assert resp.status_code == 429
        assert "Rate limit exceeded for auth" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    def test_register_rate_limit_enforced(self, client):
        limiter = get_rate_limiter()
        limiter.reset()

        # Configured auth limit is 5 attempts per window
        for i in range(5):
            resp = client.post(
                "/register",
                json={"email": f"new_rate_user_{i}@example.com", "password": "ValidPassword123!"},
            )
            assert resp.status_code in (200, 400)

        # 6th attempt must return 429
        resp = client.post(
            "/register",
            json={"email": "new_rate_user_exceeded@example.com", "password": "ValidPassword123!"},
        )
        assert resp.status_code == 429
        assert "Rate limit exceeded for auth" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    def test_login_register_normal_behavior_under_limit(self, client):
        limiter = get_rate_limiter()
        limiter.reset()

        unique_email = f"normal_user_{int(time.time())}@example.com"
        # 1. Register successfully
        reg_resp = client.post(
            "/register",
            json={"email": unique_email, "password": "StrongPassword123!", "name": "Normal User"},
        )
        assert reg_resp.status_code == 200
        assert "access_token" in reg_resp.json()

        # 2. Login successfully
        login_resp = client.post(
            "/login",
            json={"email": unique_email, "password": "StrongPassword123!"},
        )
        assert login_resp.status_code == 200
        assert "access_token" in login_resp.json()

        # 3. Invalid credentials returns 401, not 429
        invalid_resp = client.post(
            "/login",
            json={"email": unique_email, "password": "WrongPassword!"},
        )
        assert invalid_resp.status_code == 401


