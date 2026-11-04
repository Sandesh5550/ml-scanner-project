from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "demo-readiness-test-secret-key-32chars"
os.environ["AI_PROVIDER"] = "mock"

from database.orm import SessionLocal, init_db as orm_init_db
from database.init_db import init_db
from database.models import User, FoodLog, Product, Nutrition
from services.auth_service import hash_password, create_access_token
from api.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_database():
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
def phase1_user(db_session):
    email = "phase1_reliability_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Phase 1 Tester",
            hashed_password=hash_password("Password123!"),
            diet_type="diabetic",
            daily_calorie_limit=1800,
            created_at="2026-10-18 00:00:00",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=user.id)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def other_user(db_session):
    email = "other_phase1_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Other User",
            hashed_password=hash_password("Password123!"),
            daily_calorie_limit=2000,
            created_at="2026-10-18 00:00:00",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=user.id)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


def test_food_log_lifecycle_create_update_delete(phase1_user, other_user):
    """Test full food log lifecycle: create, update portion/calories, unauthorized deletion prevention, and deletion."""
    client = TestClient(app)

    # 1. Log a meal
    log_res = client.post(
        "/food-log",
        json={
            "product_name": "Granola Bar",
            "calories": 250.0,
            "sugar": 12.0,
            "fat": 8.0,
            "protein": 4.0,
            "serving_size": 100.0,
        },
        headers=phase1_user["headers"],
    )
    assert log_res.status_code == 200
    assert log_res.json()["status"] == "logged"

    # 2. Verify in today's foods
    today_res = client.get("/today", headers=phase1_user["headers"])
    assert today_res.status_code == 200
    foods = today_res.json().get("foods", [])
    entry = next((f for f in foods if f["product_name"] == "Granola Bar"), None)
    assert entry is not None
    log_id = entry["id"]
    assert entry["calories"] == 250.0

    # 3. Unauthorized user cannot update or delete this log
    unauth_put = client.put(
        f"/food-log/{log_id}",
        json={"calories": 500.0},
        headers=other_user["headers"],
    )
    assert unauth_put.status_code == 404

    unauth_del = client.delete(
        f"/food-log/{log_id}",
        headers=other_user["headers"],
    )
    assert unauth_del.status_code == 404

    # 4. Authorized update
    auth_put = client.put(
        f"/food-log/{log_id}",
        json={"product_name": "Super Granola Bar", "calories": 300.0, "sugar": 15.0},
        headers=phase1_user["headers"],
    )
    assert auth_put.status_code == 200
    assert auth_put.json()["status"] == "updated"
    assert auth_put.json()["food_log"]["product_name"] == "Super Granola Bar"
    assert auth_put.json()["food_log"]["calories"] == 300.0

    # 5. Daily report recalculates with updated values
    daily_res = client.get("/report/daily", headers=phase1_user["headers"])
    assert daily_res.status_code == 200
    daily_data = daily_res.json()
    assert daily_data["nutrition_breakdown"]["calories"]["consumed"] >= 300.0

    # 6. Delete the log
    auth_del = client.delete(
        f"/food-log/{log_id}",
        headers=phase1_user["headers"],
    )
    assert auth_del.status_code == 200
    assert auth_del.json()["deleted"] is True

    # 7. Second delete returns 404
    second_del = client.delete(
        f"/food-log/{log_id}",
        headers=phase1_user["headers"],
    )
    assert second_del.status_code == 404


def test_diet_aware_daily_and_weekly_report_consistency(phase1_user):
    """Verify that user diet restrictions (diabetic) are honored in daily and weekly report scoring."""
    client = TestClient(app)

    # Log a high-sugar product
    client.post(
        "/food-log",
        json={
            "product_name": "Sugar Candy",
            "calories": 200.0,
            "sugar": 35.0,
            "fat": 0.0,
            "protein": 0.0,
            "serving_size": 100.0,
        },
        headers=phase1_user["headers"],
    )

    daily = client.get("/report/daily", headers=phase1_user["headers"]).json()
    weekly = client.get("/report/weekly", headers=phase1_user["headers"]).json()

    assert daily["total_products_today"] >= 1
    # For a diabetic user, 35g sugar is categorized as AVOID
    assert daily["products_scanned"]["AVOID"] >= 1

    # Weekly report today's entry must match the daily report score exactly
    assert len(weekly["days"]) >= 1
    today_weekly = next((d for d in weekly["days"] if d["date"] == daily["date"]), None)
    assert today_weekly is not None
    assert today_weekly["score"] == daily["overall_score"]
    assert today_weekly["avoid_count"] == daily["products_scanned"]["AVOID"]


def test_scan_response_contract_includes_brand_barcode_ingredients(phase1_user, db_session):
    """Verify that /scan returns brand, barcode, and ingredients inside product dict."""
    client = TestClient(app)

    # Ensure a product with brand and ingredients exists
    test_barcode = "9990001112223"
    p = db_session.query(Product).filter(Product.barcode == test_barcode).first()
    if not p:
        p = Product(
            barcode=test_barcode,
            product_name="Benchmark Test Bar",
            brand="PRAMAAN Organics",
            ingredients="Rolled Oats, Almonds, Honey",
            additives="",
            nutriscore="A",
            created_at="2026-10-18 00:00:00",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        n = Nutrition(
            product_id=p.id,
            calories=180.0,
            fat=6.0,
            sugar=4.0,
            salt=0.1,
            protein=7.0,
            fiber=5.0,
            carbs=25.0,
        )
        db_session.add(n)
        db_session.commit()

    res = client.post("/scan", json={"barcode": test_barcode}, headers=phase1_user["headers"])
    assert res.status_code == 200
    prod = res.json()["product"]
    assert prod["barcode"] == test_barcode
    assert prod["brand"] == "PRAMAAN Organics"
    assert "Rolled Oats" in prod["ingredients"]


def test_food_log_zero_nutrients_preserved(phase1_user, db_session):
    """Regression test: Explicit zero values for all six nutrients remain 0.0."""
    client = TestClient(app)

    res = client.post(
        "/food-log",
        json={
            "product_name": "Zero Sugar & Fat Drink",
            "calories": 0.0,
            "fat": 0.0,
            "sugar": 0.0,
            "salt": 0.0,
            "protein": 0.0,
            "fiber": 0.0,
            "carbs": 0.0,
        },
        headers=phase1_user["headers"],
    )
    assert res.status_code == 200
    assert res.json()["status"] == "logged"

    log = (
        db_session.query(FoodLog)
        .filter(FoodLog.user_id == phase1_user["user"].id, FoodLog.product_name == "Zero Sugar & Fat Drink")
        .order_by(FoodLog.id.desc())
        .first()
    )
    assert log is not None
    assert log.calories == 0.0
    assert log.fat == 0.0
    assert log.sugar == 0.0
    assert log.salt == 0.0
    assert log.protein == 0.0
    assert log.fiber == 0.0
    assert log.carbs == 0.0


def test_food_log_missing_nutrients_remain_none(phase1_user, db_session):
    """Regression test: Missing nutrient values remain None."""
    client = TestClient(app)

    res = client.post(
        "/food-log",
        json={
            "product_name": "Minimal Logged Item",
            "calories": 120.0,
        },
        headers=phase1_user["headers"],
    )
    assert res.status_code == 200
    assert res.json()["status"] == "logged"

    log = (
        db_session.query(FoodLog)
        .filter(FoodLog.user_id == phase1_user["user"].id, FoodLog.product_name == "Minimal Logged Item")
        .order_by(FoodLog.id.desc())
        .first()
    )
    assert log is not None
    assert log.calories == 120.0
    assert log.fat is None
    assert log.sugar is None
    assert log.salt is None
    assert log.protein is None
    assert log.fiber is None
    assert log.carbs is None


def test_food_log_serving_size_scaling_with_nonzero_nutrients(phase1_user, db_session):
    """Regression test: Nonzero values are scaled correctly when serving size is provided."""
    client = TestClient(app)

    # Serving size 50g -> scale_factor = 50.0 / 100.0 = 0.5
    res = client.post(
        "/food-log",
        json={
            "product_name": "Scaled Snack Portion",
            "serving_size": 50.0,
            "calories": 200.0,
            "fat": 10.0,
            "sugar": 8.0,
            "salt": 1.0,
            "protein": 6.0,
            "fiber": 4.0,
            "carbs": 24.0,
        },
        headers=phase1_user["headers"],
    )
    assert res.status_code == 200
    assert res.json()["status"] == "logged"

    log = (
        db_session.query(FoodLog)
        .filter(FoodLog.user_id == phase1_user["user"].id, FoodLog.product_name == "Scaled Snack Portion")
        .order_by(FoodLog.id.desc())
        .first()
    )
    assert log is not None
    assert log.calories == pytest.approx(100.0)
    assert log.fat == pytest.approx(5.0)
    assert log.sugar == pytest.approx(4.0)
    assert log.salt == pytest.approx(0.5)
    assert log.protein == pytest.approx(3.0)
    assert log.fiber == pytest.approx(2.0)
    assert log.carbs == pytest.approx(12.0)
