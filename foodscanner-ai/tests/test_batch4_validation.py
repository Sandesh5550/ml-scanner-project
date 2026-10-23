from __future__ import annotations

import os
import sys
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-4-validation"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db, engine
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog, ScanHistory
from services import db_service
from services.auth_service import hash_password, create_access_token
from ml_model.predict_nutriscore import (
    MODEL_PATH,
    ModelArtifactError,
    load_bundle,
    predict_nutriscore,
    predict_nutriscore_details,
    calculate_heuristic_nutriscore,
)
from services.openfoodfacts_service import fetch_product_by_barcode
from api.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema is initialized for tests."""
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
def client():
    return TestClient(app)


def _create_user(db, email: str, name: str, password: str = "TestPass123!", calorie_limit: int = 2000):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name=name,
            hashed_password=hash_password(password),
            daily_calorie_limit=calorie_limit,
            created_at=db_service._utc_now_str(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(user_id=int(user.id))
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


# ==============================================================================
# 1. PUBLIC ENDPOINTS VALIDATION
# ==============================================================================

def test_public_endpoints(client):
    """Verify health, docs, and openapi.json are reachable publicly."""
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "running"}

    res_docs = client.get("/docs")
    assert res_docs.status_code == 200

    res_openapi = client.get("/openapi.json")
    assert res_openapi.status_code == 200
    schema = res_openapi.json()
    assert "paths" in schema
    assert "/health" in schema["paths"]
    assert "/scan" in schema["paths"]


# ==============================================================================
# 2. AUTHENTICATION WORKFLOW (REGISTER -> LOGIN -> JWT)
# ==============================================================================

def test_auth_workflow_complete(client, db_session):
    """Verify register, login, credential validation, and JWT generation."""
    email = f"b4_auth_{os.urandom(4).hex()}@example.com"
    password = "SecurePassword123!"

    # 1. Register new user
    res_reg = client.post(
        "/register",
        json={"email": email, "password": password, "name": "Batch4 User", "daily_calorie_limit": 2200},
    )
    assert res_reg.status_code == 200
    reg_data = res_reg.json()
    assert "access_token" in reg_data
    assert reg_data["token_type"] == "bearer"

    # 2. Duplicate registration rejected
    res_dup = client.post(
        "/register",
        json={"email": email, "password": password, "name": "Duplicate User"},
    )
    assert res_dup.status_code == 400
    assert "already registered" in res_dup.json().get("detail", "")

    # 3. Invalid email / short password rejected
    assert client.post("/register", json={"email": "not-an-email", "password": "short"}).status_code == 422

    # 4. Login with valid credentials
    res_login = client.post("/login", json={"email": email, "password": password})
    assert res_login.status_code == 200
    assert "access_token" in res_login.json()

    # 5. Login with invalid password rejected
    assert client.post("/login", json={"email": email, "password": "wrongpassword"}).status_code == 401

    # 6. Login with nonexistent user rejected
    assert client.post("/login", json={"email": "nobody@example.com", "password": password}).status_code == 401



# ==============================================================================
# 3. PROTECTED ENDPOINTS AUTH ENFORCEMENT
# ==============================================================================

def test_all_protected_endpoints_require_auth(client):
    """Verify all 15 private endpoints return 401 Unauthorized without valid JWT token."""
    protected_endpoints = [
        ("POST", "/scan", {"barcode": "8901234567890"}),
        ("POST", "/analyze", {"product_name": "Test", "calories": 100}),
        ("POST", "/ocr", {"image_base64": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"}),
        ("POST", "/food-log", {"product_name": "Apple", "calories": 95}),
        ("GET", "/today", None),
        ("GET", "/history", None),
        ("GET", "/stats", None),
        ("GET", "/user/profile", None),
        ("PUT", "/user/profile", {"name": "New Name"}),
        ("GET", "/report/daily", None),
        ("GET", "/report/weekly", None),
        ("GET", "/report/goal", None),
        ("POST", "/compare", {"product_a": "A", "product_b": "B"}),
        ("GET", "/search?query=snack", None),
        ("GET", "/product/8901234567890", None),
        ("GET", "/explain/8901234567890", None),
    ]

    for method, path, payload in protected_endpoints:
        if method == "POST":
            res = client.post(path, json=payload)
        elif method == "PUT":
            res = client.put(path, json=payload)
        else:
            res = client.get(path)

        assert res.status_code == 401, f"Endpoint {method} {path} should return 401 when unauthenticated"


# ==============================================================================
# 4. SCAN ≠ EAT INVARIANT VALIDATION (END-TO-END)
# ==============================================================================

def test_scan_not_eat_end_to_end(client, db_session):
    """Strictly verify scanning, analyzing, or OCR NEVER records food consumption."""
    user = _create_user(db_session, "b4_scannot_eat@pramaan.test", "ScanNotEat User")
    uid = user["user"].id

    # Baseline: 0 food logs
    initial_food_logs = db_session.query(FoodLog).filter(FoodLog.user_id == uid).count()

    # Seed test product in database
    test_barcode = "8901000000001"
    prod = db_service.get_product_by_barcode(db_session, test_barcode)
    if not prod:
        p = db_service.create_product(
            db_session,
            {"barcode": test_barcode, "product_name": "Batch4 Test Granola Bar", "brand": "Nature"},
        )
        db_service.create_nutrition(
            db_session,
            {"product_id": p.id, "calories": 200.0, "sugar": 8.0, "salt": 0.2, "fat": 6.0, "protein": 4.0},
        )
        db_session.commit()

    # 1. Barcode Scan
    res_scan = client.post("/scan", json={"barcode": test_barcode}, headers=user["headers"])
    assert res_scan.status_code == 200
    assert db_session.query(FoodLog).filter(FoodLog.user_id == uid).count() == initial_food_logs

    # 2. Manual Analysis
    res_analyze = client.post(
        "/analyze",
        json={"product_name": "Manual Granola", "calories": 190, "sugar": 7, "salt": 0.3, "fat": 5},
        headers=user["headers"],
    )
    assert res_analyze.status_code == 200
    assert db_session.query(FoodLog).filter(FoodLog.user_id == uid).count() == initial_food_logs

    # 3. OCR Scan
    tiny_gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    res_ocr = client.post("/ocr", json={"image_base64": tiny_gif}, headers=user["headers"])
    assert res_ocr.status_code == 200
    assert db_session.query(FoodLog).filter(FoodLog.user_id == uid).count() == initial_food_logs

    # 4. Explicit consumption logging (ONLY this must increment the food log count)
    res_log = client.post(
        "/food-log",
        json={"product_name": "Consumed Granola Bar", "calories": 200, "sugar": 8, "salt": 0.2, "fat": 6},
        headers=user["headers"],
    )
    assert res_log.status_code == 200
    assert db_session.query(FoodLog).filter(FoodLog.user_id == uid).count() == initial_food_logs + 1


# ==============================================================================
# 5. USER AND DATA ISOLATION VALIDATION
# ==============================================================================

def test_user_and_data_isolation(client, db_session):
    """Verify that user data (logs, reports, history, profiles) are completely isolated."""
    user_a = _create_user(db_session, "user_a@pramaan.test", "User Alpha", calorie_limit=1800)
    user_b = _create_user(db_session, "user_b@pramaan.test", "User Beta", calorie_limit=2400)

    # User A logs an item
    res_log_a = client.post(
        "/food-log",
        json={"product_name": "Alpha Private Meal", "calories": 650.0},
        headers=user_a["headers"],
    )
    assert res_log_a.status_code == 200

    # User B queries /today -> must NOT see User A's meal
    res_today_b = client.get("/today", headers=user_b["headers"])
    assert res_today_b.status_code == 200
    foods_b = res_today_b.json().get("foods", [])
    food_names_b = [f.get("product_name") for f in foods_b]
    assert "Alpha Private Meal" not in food_names_b

    # User A profile update does not affect User B
    client.put("/user/profile", json={"name": "Alpha Updated", "weight": 78.5}, headers=user_a["headers"])
    res_profile_b = client.get("/user/profile", headers=user_b["headers"])
    assert res_profile_b.status_code == 200
    assert res_profile_b.json()["name"] == "User Beta"
    assert res_profile_b.json()["daily_calorie_limit"] == 2400


# ==============================================================================
# 6. ML MODEL AND ARTIFACT VALIDATION
# ==============================================================================

def test_ml_model_artifacts_and_metrics():
    """Verify ML artifact loads, metadata contains held-out test metrics, and inference is deterministic."""
    assert MODEL_PATH.exists(), f"Model artifact missing at {MODEL_PATH}"
    bundle = load_bundle(MODEL_PATH)

    assert "model" in bundle
    assert "features" in bundle
    assert "metrics" in bundle
    assert "classes" in bundle
    assert bundle["classes"] == ["a", "b", "c", "d", "e"]

    # Held-out test accuracy documentation verification (~82.69% held-out test set accuracy)
    test_acc = bundle["metrics"].get("test_accuracy")
    assert test_acc is not None
    assert 0.80 <= test_acc <= 0.85, f"Expected held-out test accuracy around ~82.69%, got {test_acc}"

    # Verify deterministic output
    sample = {"calories": 40.0, "fat": 0.5, "sugar": 3.0, "salt": 0.05, "protein": 1.0, "fiber": 2.5, "carbs": 8.0}
    score1 = predict_nutriscore(sample)
    score2 = predict_nutriscore(sample)
    assert score1 == score2 == "A"

    # Verify probability distribution
    details = predict_nutriscore_details(sample)
    probs = details["probabilities"]
    assert len(probs) == 5
    assert abs(sum(probs.values()) - 1.0) < 0.01

    # Verify missing artifact raises explicit error
    with pytest.raises(FileNotFoundError):
        predict_nutriscore(sample, model_path=Path("nonexistent_model.pkl"))


# ==============================================================================
# 7. OPENFOODFACTS SERVICE INTEGRATION & RESILIENCE VALIDATION
# ==============================================================================

def test_openfoodfacts_resilience():
    """Verify OpenFoodFacts handles network errors and timeouts gracefully."""
    # 1. Timeout gracefully handled
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Timeout")):
        res_timeout = fetch_product_by_barcode("8901234567890")
        assert res_timeout is None

    # 2. Network error gracefully handled
    with patch("httpx.Client.get", side_effect=httpx.NetworkError("DNS fail")):
        res_net = fetch_product_by_barcode("8901234567890")
        assert res_net is None

    # 3. 404 Not Found gracefully handled
    mock_404 = MagicMock(status_code=404)
    with patch("httpx.Client.get", return_value=mock_404):
        res_404 = fetch_product_by_barcode("0000000000000")
        assert res_404 is None
