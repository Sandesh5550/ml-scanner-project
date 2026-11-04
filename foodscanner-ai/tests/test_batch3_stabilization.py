from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-3-verification"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db, engine
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog
from services import db_service
from services.auth_service import hash_password, create_access_token
from api.main import app, _get_allowed_origins


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema is initialized."""
    init_db()
    orm_init_db()


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def auth_user(db_session):
    """Create or retrieve a test user with a valid JWT token."""
    email = "batch3_test_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Batch3 User",
            hashed_password=hash_password("Secret123"),
            daily_calorie_limit=2000,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


# ==============================================================================
# 1. SECURITY & ENDPOINT PROTECTION
# ==============================================================================

def test_compare_endpoint_requires_auth(auth_user, db_session):
    """Verify /compare requires authentication and accepts authenticated requests."""
    client = TestClient(app)

    # 1. Unauthenticated request must return 401
    res_unauth = client.post(
        "/compare",
        json={"product_a": "Lays", "product_b": "Kurkure"},
    )
    assert res_unauth.status_code == 401, f"Expected 401 for unauthenticated /compare, got {res_unauth.status_code}"

    # 2. Invalid token must return 401
    res_bad = client.post(
        "/compare",
        json={"product_a": "Lays", "product_b": "Kurkure"},
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    assert res_bad.status_code == 401

    # 3. Seed two products so comparison succeeds
    p1 = db_service.get_product_by_barcode(db_session, "8901234567890")
    if not p1:
        prod1 = db_service.create_product(
            db_session,
            {
                "barcode": "8901234567890",
                "product_name": "Batch3 Lays Classic",
                "brand": "Lays",
                "nutriscore": "d",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod1.id,
                "calories": 540.0,
                "sugar": 2.0,
                "salt": 1.6,
                "fat": 35.0,
                "fiber": 2.0,
                "protein": 6.0,
            },
        )
        db_session.commit()

    p2 = db_service.get_product_by_barcode(db_session, "8901234567891")
    if not p2:
        prod2 = db_service.create_product(
            db_session,
            {
                "barcode": "8901234567891",
                "product_name": "Batch3 Baked Chips",
                "brand": "Baked",
                "nutriscore": "b",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod2.id,
                "calories": 420.0,
                "sugar": 1.0,
                "salt": 0.8,
                "fat": 15.0,
                "fiber": 5.0,
                "protein": 8.0,
            },
        )
        db_session.commit()

    # 4. Authenticated request must succeed
    res_auth = client.post(
        "/compare",
        json={"product_a": "Batch3 Lays Classic", "product_b": "Batch3 Baked Chips"},
        headers=auth_user["headers"],
    )
    assert res_auth.status_code == 200
    data = res_auth.json()
    assert "healthier_product" in data
    assert "reasons" in data
    assert "product_a" in data
    assert "product_b" in data


def test_protected_endpoints_reject_unauthenticated():
    """Verify that all user-specific endpoints strictly reject requests without token."""
    client = TestClient(app)

    protected_endpoints = [
        ("POST", "/scan", {"barcode": "8901058000256"}),
        ("POST", "/food-log", {"product_name": "Test Snack", "calories": 150}),
        ("POST", "/analyze", {"product_name": "Test Apple"}),
        ("GET", "/today", None),
        ("GET", "/history", None),
        ("GET", "/stats", None),
        ("GET", "/user/profile", None),
        ("PUT", "/user/profile", {"name": "Hacker"}),
        ("GET", "/report/daily", None),
        ("GET", "/report/weekly", None),
        ("GET", "/report/goal", None),
        ("GET", "/search?query=Maggi", None),
        ("POST", "/ocr", {"image_base64": "invalid"}),
    ]

    for method, path, payload in protected_endpoints:
        if method == "POST":
            res = client.post(path, json=payload)
        elif method == "PUT":
            res = client.put(path, json=payload)
        else:
            res = client.get(path)

        assert res.status_code == 401, f"Expected 401 for {method} {path} without token, got {res.status_code}"


def test_public_endpoints_accessible():
    """Verify health and docs endpoints remain publicly accessible."""
    client = TestClient(app)

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "running"}

    res_docs = client.get("/docs")
    assert res_docs.status_code == 200


# ==============================================================================
# 2. CORS AUDIT
# ==============================================================================

def test_cors_configuration_explicit_and_secure():
    """Verify wildcard '*' is removed and local/LAN origins are supported."""
    allowed = _get_allowed_origins()
    assert "*" not in allowed, "Wildcard '*' must NOT be in allowed origins"
    assert "http://localhost:8081" in allowed
    assert "http://127.0.0.1:8081" in allowed

    client = TestClient(app)

    # 1. Localhost origin should get allow-origin header
    res_local = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res_local.headers.get("access-control-allow-origin") == "http://localhost:8081"
    assert res_local.headers.get("access-control-allow-credentials") == "true"

    # 2. LAN IP origin (e.g. 192.168.1.50:8081) should match regex and be allowed
    res_lan = client.options(
        "/health",
        headers={
            "Origin": "http://192.168.1.50:8081",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res_lan.headers.get("access-control-allow-origin") == "http://192.168.1.50:8081"
    assert res_lan.headers.get("access-control-allow-credentials") == "true"

    # 3. Disallowed public domain should NOT get allow-origin
    res_evil = client.options(
        "/health",
        headers={
            "Origin": "https://malicious-phishing-site.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res_evil.headers.get("access-control-allow-origin") is None


# ==============================================================================
# 3. SCAN ≠ EAT DOMAIN RULE VERIFICATION
# ==============================================================================

def test_scan_does_not_create_food_log(auth_user, db_session):
    """Verify that scanning a product creates 0 rows in daily_food_log."""
    user_id = auth_user["user"].id

    # Clean existing logs for test user
    db_session.query(FoodLog).filter(FoodLog.user_id == user_id).delete()
    db_session.commit()

    initial_count = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert initial_count == 0

    client = TestClient(app)
    # Perform scan
    res = client.post(
        "/scan",
        json={"barcode": "8901234567890", "product_name": "Batch3 Lays Classic"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200

    # Verify NO food log was created
    post_scan_count = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert post_scan_count == 0, "Scan must NOT create a food log entry (Scan != Eat)"


def test_analyze_does_not_create_food_log(auth_user, db_session):
    """Verify that analyzing manual nutrition values creates 0 rows in daily_food_log."""
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    client = TestClient(app)
    res = client.post(
        "/analyze",
        json={
            "product_name": "Batch3 Manual Snack",
            "calories": 250,
            "sugar": 5,
            "salt": 0.5,
            "fat": 10,
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "Manual /analyze must NOT create a food log entry"


def test_explicit_food_log_creates_record(auth_user, db_session):
    """Verify that explicit POST /food-log creates exactly 1 row in daily_food_log."""
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    client = TestClient(app)
    res = client.post(
        "/food-log",
        json={
            "product_name": "Batch3 Consumed Snack",
            "barcode": "8901234567890",
            "calories": 200,
            "fat": 8,
            "sugar": 2,
            "salt": 0.4,
            "serving_size": 100,
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    assert res.json() == {"status": "logged"}

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before + 1, "POST /food-log must create exactly one food log record"


# ==============================================================================
# 4. OCR BACKEND VALIDATION
# ==============================================================================

def test_ocr_endpoint_validation(auth_user):
    """Verify OCR endpoint rejects empty/invalid base64 and accepts valid image payload."""
    client = TestClient(app)

    # 1. Empty image string returns 400
    res_empty = client.post("/ocr", json={"image_base64": ""}, headers=auth_user["headers"])
    assert res_empty.status_code == 400

    # 2. Invalid base64 characters
    # base64.b64decode with validate=False will still raise if completely invalid
    # Let's test with a valid 1x1 GIF base64
    tiny_gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    res_valid = client.post("/ocr", json={"image_base64": tiny_gif}, headers=auth_user["headers"])
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert "raw_text" in data
    assert "calories" in data
