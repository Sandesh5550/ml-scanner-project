from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from jose import jwt

# Ensure foodscanner-ai is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Ensure a safe, predictable test secret key and mock provider are set for pytest
os.environ["SECRET_KEY"] = "demo-readiness-test-secret-key-32chars"
os.environ["AI_PROVIDER"] = "mock"

from database.orm import SessionLocal, init_db as orm_init_db
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.config import get_secret_key, validate_runtime_config, load_environment
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
def demo_user(db_session):
    """Create or retrieve a clean demo readiness user."""
    email = "demoreadiness_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Demo Readiness User",
            hashed_password=hash_password("DemoPassword123!"),
            daily_calorie_limit=2000,
            age=25,
            weight=70.0,
            height=175.0,
            diet_type="balanced",
            goal_type="maintenance",
            goal_target_days=30,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    else:
        user.age = 25
        user.weight = 70.0
        user.height = 175.0
        user.diet_type = "balanced"
        user.goal_type = "maintenance"
        user.daily_calorie_limit = 2000
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "email": email,
        "password": "DemoPassword123!",
    }


@pytest.fixture
def seed_demo_product(db_session):
    """Seed a sample product for smoke testing."""
    barcode = "8901234567890"
    prod = db_service.get_product_by_barcode(db_session, barcode)
    if not prod:
        product_obj = db_service.create_product(
            db_session,
            {
                "barcode": barcode,
                "product_name": "Demo Whole Grain Rolled Oats",
                "brand": "NutriDemo",
                "nutriscore": "a",
                "ingredients": "Whole grain rolled oats",
                "additives": None,
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": product_obj.id,
                "calories": 389.0,
                "fat": 6.9,
                "saturated_fat": 1.2,
                "sugar": 0.99,
                "salt": 0.02,
                "protein": 16.89,
                "fiber": 10.6,
                "carbs": 66.3,
            },
        )
        db_session.commit()
    return barcode


# ==============================================================================
# 1. CONFIGURATION & STARTUP VALIDATION TESTS (PHASE 11)
# ==============================================================================

def test_config_validation_success_when_secret_key_present():
    """Verify runtime configuration validates successfully when SECRET_KEY is set."""
    config = validate_runtime_config(raise_error=True)
    assert config["valid"] is True
    assert config["secret_key_set"] is True
    assert "SECRET_KEY" not in str(config.values())  # Never leak actual secret


def test_config_validation_fails_early_when_secret_key_missing():
    """Verify clear error is raised early if SECRET_KEY is missing."""
    with patch.dict(os.environ, {"SECRET_KEY": "", "ALLOW_INSECURE_DEV_AUTH": ""}, clear=False):
        # Must raise RuntimeError with helpful instruction
        with pytest.raises(RuntimeError) as exc_info:
            validate_runtime_config(raise_error=True)
        assert "SECRET_KEY is not configured" in str(exc_info.value)
        assert "foodscanner-ai/.env" in str(exc_info.value)


def test_config_secret_key_not_silently_regenerated():
    """Verify SECRET_KEY is deterministic and not randomly regenerated every call."""
    key1 = get_secret_key()
    key2 = get_secret_key()
    assert key1 == key2
    assert len(key1) > 0


def test_jwt_signature_verification_with_configured_secret():
    """Verify JWT encoded with create_access_token verifies against configured secret."""
    token = create_access_token(user_id=42)
    secret = get_secret_key()
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    assert payload["sub"] == "42"
    assert "exp" in payload


# ==============================================================================
# 2. AUTHENTICATION END-TO-END VALIDATION (PHASE 4 & 7)
# ==============================================================================

def test_auth_full_lifecycle(db_session):
    """Verify registration -> login -> JWT token -> authenticated profile request."""
    client = TestClient(app)
    unique_email = f"lifecycle_{datetime.now(timezone.utc).timestamp()}@example.com"
    raw_password = "SecurePassword123!"

    # 1. Register
    reg_res = client.post(
        "/register",
        json={"email": unique_email, "password": raw_password, "name": "Lifecycle Tester"},
    )
    assert reg_res.status_code == 200
    reg_data = reg_res.json()
    assert "access_token" in reg_data
    assert reg_data["token_type"] == "bearer"

    # 2. Login
    login_res = client.post(
        "/login",
        json={"email": unique_email, "password": raw_password},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    token = login_data["access_token"]
    assert token

    # 3. Access protected endpoint
    headers = {"Authorization": f"Bearer {token}"}
    profile_res = client.get("/user/profile", headers=headers)
    assert profile_res.status_code == 200
    profile_data = profile_res.json()
    assert profile_data["email"] == unique_email


def test_auth_invalid_credentials_rejected():
    """Verify login with incorrect password returns 401."""
    client = TestClient(app)
    res = client.post("/login", json={"email": "nonexistent@example.com", "password": "wrong"})
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_auth_missing_token_returns_401():
    """Verify calling protected endpoint without token returns 401."""
    client = TestClient(app)
    res = client.get("/user/profile")
    assert res.status_code == 401
    assert "Missing Authorization token" in res.json()["detail"]


def test_auth_malformed_token_returns_401():
    """Verify invalid/corrupted token returns 401."""
    client = TestClient(app)
    res = client.get("/user/profile", headers={"Authorization": "Bearer not-a-valid-jwt-token"})
    assert res.status_code == 401
    assert "Invalid or expired token" in res.json()["detail"]


def test_auth_expired_token_returns_401():
    """Verify expired token returns 401."""
    client = TestClient(app)
    secret = get_secret_key()
    expired_payload = {
        "sub": "1",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    expired_token = jwt.encode(expired_payload, secret, algorithm="HS256")
    res = client.get("/user/profile", headers={"Authorization": f"Bearer {expired_token}"})
    assert res.status_code == 401
    assert "Invalid or expired token" in res.json()["detail"]


def test_auth_user_isolation(demo_user, db_session):
    """Verify User A cannot view User B's scans or food logs."""
    client = TestClient(app)

    # Create User B
    email_b = f"user_b_{datetime.now(timezone.utc).timestamp()}@example.com"
    user_b = User(
        email=email_b,
        name="User B",
        hashed_password=hash_password("PassB123!"),
        daily_calorie_limit=1800,
        created_at=db_service._utc_now_str(),
    )
    db_session.add(user_b)
    db_session.commit()
    db_session.refresh(user_b)
    token_b = create_access_token(user_id=int(user_b.id))
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User B logs a specific food
    b_log = client.post(
        "/food-log",
        json={"product_name": "User B Secret Apple", "calories": 95.0},
        headers=headers_b,
    )
    assert b_log.status_code == 200

    # User A checks /today; must NOT see User B's logged apple
    a_today = client.get("/today", headers=demo_user["headers"])
    assert a_today.status_code == 200
    a_foods = [f.get("food_name") or f.get("product_name") for f in a_today.json().get("foods", [])]
    assert "User B Secret Apple" not in a_foods


# ==============================================================================
# 3. SWAGGER / OPENAPI VALIDATION (PHASE 8)
# ==============================================================================

def test_openapi_schema_and_security():
    """Verify /openapi.json loads with valid HTTPBearer security scheme and models."""
    client = TestClient(app)
    res = client.get("/openapi.json")
    assert res.status_code == 200
    spec = res.json()

    # Basic API metadata
    assert spec["info"]["title"] == "FoodScanner AI API"
    assert "paths" in spec

    # Security Schemes
    components = spec.get("components", {})
    security_schemes = components.get("securitySchemes", {})
    assert "HTTPBearer" in security_schemes
    assert security_schemes["HTTPBearer"]["type"] == "http"
    assert security_schemes["HTTPBearer"]["scheme"] == "bearer"

    # Protected paths require HTTPBearer
    chat_post = spec["paths"].get("/chat", {}).get("post", {})
    assert "security" in chat_post
    assert any("HTTPBearer" in sec for sec in chat_post["security"])

    scan_post = spec["paths"].get("/scan", {}).get("post", {})
    assert "security" in scan_post
    assert any("HTTPBearer" in sec for sec in scan_post["security"])


# ==============================================================================
# 4. SCAN ≠ EAT PERMANENT INVARIANT (PHASE 6)
# ==============================================================================

def test_scan_not_eat_invariant_across_all_read_endpoints(demo_user, seed_demo_product, db_session):
    """Verify scanning/analyzing/querying does NOT log food consumption.
    Only explicit POST /food-log records consumption.
    """
    client = TestClient(app)
    user_id = demo_user["user"].id
    barcode = seed_demo_product

    # Helper to count food log entries
    def get_log_count():
        return db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    initial_count = get_log_count()

    # 1. POST /scan
    res = client.post("/scan", json={"barcode": barcode}, headers=demo_user["headers"])
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 2. POST /analyze
    res = client.post(
        "/analyze",
        json={"product_name": "Test Snack", "calories": 200.0, "sugar": 5.0, "fat": 2.0},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 3. POST /alternatives
    res = client.post(
        "/alternatives",
        json={"product_name": "Demo Whole Grain Rolled Oats", "barcode": barcode},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 4. POST /compare
    res = client.post(
        "/compare",
        json={"product_a": barcode, "product_b": barcode},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 5. POST /verify-claims
    res = client.post(
        "/verify-claims",
        json={"claims": ["high protein", "low sugar"], "barcode": barcode},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 6. POST /chat
    res = client.post(
        "/chat",
        json={"message": "Is this healthy?", "barcode": barcode},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 7. GET /product/{barcode}
    res = client.get(f"/product/{barcode}", headers=demo_user["headers"])
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # 8. GET /explain/{barcode}
    res = client.get(f"/explain/{barcode}", headers=demo_user["headers"])
    assert res.status_code == 200
    assert get_log_count() == initial_count

    # NOW: Explicit food log intake
    log_res = client.post(
        "/food-log",
        json={"product_name": "Intentional Intake Oatmeal", "calories": 250.0},
        headers=demo_user["headers"],
    )
    assert log_res.status_code == 200

    # Must increment by exactly 1
    assert get_log_count() == initial_count + 1


# ==============================================================================
# 5. CRITICAL API SMOKE TESTS (PHASE 5)
# ==============================================================================

def test_smoke_health_endpoint():
    """Verify GET /health returns 200 running."""
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "running"}


def test_smoke_scan_endpoint(demo_user, seed_demo_product):
    """Verify POST /scan returns 200 with score and recommendations."""
    client = TestClient(app)
    res = client.post("/scan", json={"barcode": seed_demo_product}, headers=demo_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert "product" in data
    assert "analysis" in data
    assert "decision" in data
    assert "health_score" in data["analysis"]
    assert "final_decision" in data["decision"]


def test_smoke_analyze_endpoint(demo_user):
    """Verify POST /analyze returns 200 with score analysis."""
    client = TestClient(app)
    payload = {
        "product_name": "Greek Yogurt",
        "calories": 130.0,
        "fat": 4.0,
        "saturated_fat": 2.5,
        "sugar": 6.0,
        "salt": 0.1,
        "protein": 12.0,
        "fiber": 0.0,
        "carbs": 8.0,
    }
    res = client.post("/analyze", json=payload, headers=demo_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert "product" in data
    assert "analysis" in data
    assert "health_score" in data["analysis"]
    assert data["product"]["name"] == "Greek Yogurt"


def test_smoke_ocr_endpoint_handles_missing_file(demo_user):
    """Verify POST /ocr handles missing or empty image gracefully."""
    client = TestClient(app)
    res = client.post("/ocr", headers=demo_user["headers"])
    # 422 because image file is required
    assert res.status_code == 422


def test_smoke_compare_endpoint(demo_user, seed_demo_product):
    """Verify POST /compare returns comparison metrics."""
    client = TestClient(app)
    res = client.post(
        "/compare",
        json={"product_a": seed_demo_product, "product_b": seed_demo_product},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "product_a" in data
    assert "product_b" in data
    assert "healthier_product" in data


def test_smoke_alternatives_endpoint(demo_user, seed_demo_product):
    """Verify POST /alternatives returns recommendation list."""
    client = TestClient(app)
    res = client.post(
        "/alternatives",
        json={"product_name": "Demo Whole Grain Rolled Oats", "limit": 2},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "alternatives" in data
    assert isinstance(data["alternatives"], list)


def test_smoke_verify_claims_endpoint(demo_user, seed_demo_product):
    """Verify POST /verify-claims returns claim assessment."""
    client = TestClient(app)
    res = client.post(
        "/verify-claims",
        json={"claims": ["High Protein"], "barcode": seed_demo_product},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "results" in data
    assert "total_claims" in data


def test_smoke_chat_endpoint(demo_user, seed_demo_product):
    """Verify POST /chat returns grounded answer and source citations."""
    client = TestClient(app)
    res = client.post(
        "/chat",
        json={"message": "Is this product good for energy?", "barcode": seed_demo_product},
        headers=demo_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "answer" in data
    assert "sources" in data
    assert data["product_context_used"] is True


def test_smoke_food_log_and_today(demo_user):
    """Verify POST /food-log and GET /today."""
    client = TestClient(app)
    log_res = client.post(
        "/food-log",
        json={"product_name": "Morning Banana", "calories": 105.0},
        headers=demo_user["headers"],
    )
    assert log_res.status_code == 200

    today_res = client.get("/today", headers=demo_user["headers"])
    assert today_res.status_code == 200
    today_data = today_res.json()
    assert "calories_consumed_today" in today_data
    assert "remaining_calories" in today_data
    assert "foods" in today_data


def test_smoke_history_and_stats(demo_user):
    """Verify GET /history and GET /stats."""
    client = TestClient(app)
    hist_res = client.get("/history", headers=demo_user["headers"])
    assert hist_res.status_code == 200
    assert isinstance(hist_res.json(), list)

    stats_res = client.get("/stats", headers=demo_user["headers"])
    assert stats_res.status_code == 200
    assert "total_scans_ever" in stats_res.json()


def test_smoke_profile_endpoints(demo_user):
    """Verify GET /user/profile and PUT /user/profile."""
    client = TestClient(app)
    get_res = client.get("/user/profile", headers=demo_user["headers"])
    assert get_res.status_code == 200
    assert get_res.json()["email"] == demo_user["email"]

    put_res = client.put(
        "/user/profile",
        json={"daily_calorie_limit": 2100, "diet_type": "vegan"},
        headers=demo_user["headers"],
    )
    assert put_res.status_code == 200
    assert put_res.json()["daily_calorie_limit"] == 2100


def test_smoke_reports(demo_user):
    """Verify report endpoints: daily, weekly, goal."""
    client = TestClient(app)
    daily = client.get("/report/daily", headers=demo_user["headers"])
    assert daily.status_code == 200

    weekly = client.get("/report/weekly", headers=demo_user["headers"])
    assert weekly.status_code == 200

    goal = client.get("/report/goal", headers=demo_user["headers"])
    assert goal.status_code == 200


def test_smoke_search_endpoint(demo_user):
    """Verify GET /search returns product search results."""
    client = TestClient(app)
    res = client.get("/search?query=oats", headers=demo_user["headers"])
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_smoke_product_and_explain_endpoints(demo_user, seed_demo_product):
    """Verify GET /product/{barcode} and GET /explain/{barcode}."""
    client = TestClient(app)
    barcode = seed_demo_product
    prod_res = client.get(f"/product/{barcode}", headers=demo_user["headers"])
    assert prod_res.status_code == 200
    assert prod_res.json()["product"]["barcode"] == barcode

    exp_res = client.get(f"/explain/{barcode}", headers=demo_user["headers"])
    assert exp_res.status_code == 200
    exp_data = exp_res.json()
    assert "final_score" in exp_data
    assert "steps" in exp_data
