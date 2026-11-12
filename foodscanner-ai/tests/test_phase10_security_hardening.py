from __future__ import annotations

import os
import time
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app
from database.db_session import get_db
from database.models import User
from services.auth_service import (
    create_access_token,
    create_refresh_token,
    get_revocation_store,
    is_token_revoked,
    reset_revocation_store,
    revoke_token,
    verify_access_token,
    verify_refresh_token,
)


@pytest.fixture(autouse=True)
def clean_revocation_store():
    reset_revocation_store()
    yield
    reset_revocation_store()


def test_token_types_and_lifecycle():
    access_tok = create_access_token(user_id=123, expires_delta=timedelta(minutes=15))
    refresh_tok = create_refresh_token(user_id=123, expires_delta=timedelta(days=7))

    # Access token verification
    assert verify_access_token(access_tok) == 123

    # Reject refresh token used as access token
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(refresh_tok)
    assert exc_info.value.status_code == 401
    assert "cannot use refresh token" in exc_info.value.detail.lower()

    # Refresh token verification
    assert verify_refresh_token(refresh_tok) == 123

    # Reject access token used as refresh token
    with pytest.raises(HTTPException) as exc_info:
        verify_refresh_token(access_tok)
    assert exc_info.value.status_code == 401
    assert "expected refresh token" in exc_info.value.detail.lower()


def test_token_revocation_and_blocklist():
    tok = create_access_token(user_id=456)
    assert is_token_revoked(tok) is False
    assert verify_access_token(tok) == 456

    # Revoke token
    revoked = revoke_token(tok)
    assert revoked is True
    assert is_token_revoked(tok) is True

    # Verification fails on revoked token
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(tok)
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


def test_auth_refresh_endpoint(monkeypatch):
    test_user = User(id=777, email="test_refresh@pramaan.ai", name="Refresh User")

    class MockUserQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return test_user

    mock_db = MagicMock()
    mock_db.query.return_value = MockUserQuery()
    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)
    refresh_token = create_refresh_token(user_id=777)

    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"

    # Verify newly issued tokens
    new_uid = verify_access_token(data["access_token"])
    assert new_uid == 777

    app.dependency_overrides.clear()


def test_auth_logout_endpoint():
    tok = create_access_token(user_id=888)
    client = TestClient(app)

    resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_out"

    # Token is now revoked
    assert is_token_revoked(tok) is True
    with pytest.raises(HTTPException) as exc_info:
        verify_access_token(tok)
    assert exc_info.value.status_code == 401


def test_security_headers_middleware():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200

    headers = resp.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("X-XSS-Protection") == "1; mode=block"
    assert "strict-origin" in headers.get("Referrer-Policy", "").lower()
    assert "max-age=" in headers.get("Strict-Transport-Security", "").lower()


def test_request_payload_size_limit_middleware(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_SIZE", "1024")  # 1 KB limit
    client = TestClient(app)

    # Small payload succeeds
    resp = client.post("/search", json={"query": "milk"}, headers={"content-length": "20"})
    assert resp.status_code != 413

    # Exceeding payload fails with 413 Payload Too Large
    resp = client.post("/search", json={"query": "x" * 2000}, headers={"content-length": "2050"})
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


def test_chunked_streaming_request_body_limit(monkeypatch):
    """Enforces byte limit on requests with no Content-Length header or chunked transfer."""
    monkeypatch.setenv("MAX_REQUEST_BODY_SIZE", "512")  # 512 bytes limit
    client = TestClient(app)

    # Payload within 512 bytes (without explicit Content-Length)
    resp = client.request(
        "POST",
        "/search",
        content=b'{"query": "organic tea"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code != 413

    # Payload exceeding 512 bytes without Content-Length header
    oversized_data = b'{"query": "' + (b"A" * 600) + b'"}'
    resp = client.request(
        "POST",
        "/search",
        content=oversized_data,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


def test_refresh_token_rotation_and_replay_detection(monkeypatch):
    """Verify that a refresh token can only be used once, and replay attempts return 401."""
    test_user = User(id=999, email="replay_test@pramaan.ai", name="Replay User")

    class MockUserQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return test_user

    mock_db = MagicMock()
    mock_db.query.return_value = MockUserQuery()
    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)
    original_refresh = create_refresh_token(user_id=999)

    # 1. First use succeeds
    resp1 = client.post("/auth/refresh", json={"refresh_token": original_refresh})
    assert resp1.status_code == 200
    data1 = resp1.json()
    new_refresh = data1["refresh_token"]
    assert new_refresh != original_refresh

    # 2. Second use of same original_refresh MUST be rejected as replay attempt
    resp2 = client.post("/auth/refresh", json={"refresh_token": original_refresh})
    assert resp2.status_code == 401
    assert "revoked" in resp2.json()["detail"].lower()

    # 3. New rotated refresh token succeeds
    resp3 = client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert resp3.status_code == 200

    app.dependency_overrides.clear()


def test_token_revocation_store_redis_wiring():
    """Verify TokenRevocationStore correctly delegates to Redis when wired."""
    fake_redis_dict = {}

    class MockRedisStore:
        def set(self, key, value, ex=None, nx=False):
            if nx and key in fake_redis_dict:
                return False
            fake_redis_dict[key] = value
            return True

        def setex(self, key, ttl, value):
            fake_redis_dict[key] = value
            return True

        def get(self, key):
            return fake_redis_dict.get(key)

        def exists(self, key):
            return 1 if key in fake_redis_dict else 0

        def ping(self):
            return True

    mock_redis = MockRedisStore()
    store = get_revocation_store()
    store.set_redis_client(mock_redis)

    tok = create_access_token(user_id=333)
    assert store.is_revoked(tok) is False

    store.revoke_token(tok)
    assert store.is_revoked(tok) is True
    # Verify presence in Redis storage
    assert any(k.startswith("revoked:") for k in fake_redis_dict.keys())

    # Disconnect Redis and ensure local store state remains safe
    store.set_redis_client(None)
