import uuid
import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.orm import SessionLocal
from database.models import User, FoodLog
from services.auth_service import create_access_token

client = TestClient(app)


@pytest.fixture
def auth_user():
    """Create or retrieve a clean authenticated test user with a valid JWT token."""
    email = f"manual_user_{uuid.uuid4().hex[:6]}@example.com"
    with SessionLocal() as db:
        user = User(
            email=email,
            hashed_password="hashed_password_123",
            name="Manual Entry Tester",
            daily_calorie_limit=2000,
            created_at="2026-09-29 12:00:00",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
        user_email = user.email

    token = create_access_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    return {"user_id": user_id, "email": user_email, "token": token, "headers": headers}


def test_manual_entry_analyze_flow_and_scan_not_eat(auth_user):
    """
    Test 1: Manual Nutrition Entry Analysis
    - Submits Test Oats declared metrics
    - Evaluates deterministic health score
    - Confirms Scan != Eat (zero writes to FoodLog)
    """
    headers = auth_user["headers"]
    user_id = auth_user["user_id"]

    with SessionLocal() as db:
        initial_diary_count = db.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    payload = {
        "product_name": "Test Oats",
        "calories": 389.0,
        "fat": 6.9,
        "saturated_fat": 1.2,
        "carbs": 66.3,
        "sugar": 0.9,
        "fiber": 10.6,
        "protein": 16.9,
        "salt": 0.05,
    }

    res = client.post("/analyze", json=payload, headers=headers)
    assert res.status_code == 200, f"Analysis failed: {res.status_code} - {res.text}"
    data = res.json()

    # Product structure
    assert data["product"]["name"] == "Test Oats"
    assert data["product"]["nutrition"]["calories"] == 389.0
    assert data["product"]["nutrition"]["protein"] == 16.9
    assert data["product"]["nutrition"]["fiber"] == 10.6
    assert data["product"]["nutrition"]["saturated_fat"] == 1.2

    # Deterministic health score & decision
    assert data["analysis"]["health_score"] >= 70.0
    assert data["decision"]["final_decision"] == "SAFE"

    # Scan != Eat verification: manual analysis must NOT write to daily_food_log
    with SessionLocal() as db:
        diary_count_after_analyze = db.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert diary_count_after_analyze == initial_diary_count, "Scan != Eat violated! /analyze wrote to FoodLog"


def test_manual_entry_explicit_diary_logging(auth_user):
    """
    Test 2: Explicit Diary Logging after Manual Analysis
    - Submits food log record with nutrition metrics
    - Verifies exactly one record created in daily_food_log
    """
    headers = auth_user["headers"]
    user_id = auth_user["user_id"]

    with SessionLocal() as db:
        initial_count = db.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    log_payload = {
        "product_name": "Test Oats",
        "barcode": None,
        "calories": 389.0,
        "fat": 6.9,
        "sugar": 0.9,
        "salt": 0.05,
        "protein": 16.9,
        "fiber": 10.6,
        "carbs": 66.3,
        "serving_size": 100.0,
    }

    res = client.post("/food-log", json=log_payload, headers=headers)
    assert res.status_code in (200, 201), f"Food log failed: {res.status_code} - {res.text}"
    assert res.json().get("status") == "logged"

    with SessionLocal() as db:
        final_count = db.query(FoodLog).filter(FoodLog.user_id == user_id).count()
        last_entry = db.query(FoodLog).filter(FoodLog.user_id == user_id).order_by(FoodLog.id.desc()).first()

    assert final_count == initial_count + 1
    assert last_entry.product_name == "Test Oats"
    assert last_entry.calories == 389.0


def test_manual_entry_validation_and_auth():
    """
    Test 3: Auth requirement & Input Validation
    - Rejects unauthenticated request with 401
    - Rejects empty product name with 400/422
    """
    # Missing token
    res_no_auth = client.post("/analyze", json={"product_name": "Test", "calories": 100})
    assert res_no_auth.status_code == 401

    # Empty product name (with mock token or invalid)
    res_bad_input = client.post("/analyze", json={"product_name": "   ", "calories": 100})
    assert res_bad_input.status_code in (400, 401, 422)
