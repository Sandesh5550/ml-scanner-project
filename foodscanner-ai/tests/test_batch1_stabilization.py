from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-1-verification"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db, engine
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog, ScanHistory
from services import db_service
from services.auth_service import hash_password, verify_password, create_access_token, verify_access_token
from services.barcode_lookup import lookup_product
from services.food_health_score import compute_food_health_score, compute_diet_aware_score
from services.decision_explainer import build_decision_reasons
from services.ingredient_analyzer import analyze_ingredients
from api.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema and migrations are initialized."""
    init_db()
    orm_init_db()


def test_database_initialization():
    """1 & 2. Verify database initializes and all required columns exist."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    for expected_table in ("users", "products", "nutrition", "scan_history", "daily_food_log"):
        assert expected_table in table_names, f"Table {expected_table} missing"

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    for col in ("id", "name", "email", "hashed_password", "age", "weight", "height", "goal_type", "goal_target_days"):
        assert col in user_cols, f"Column {col} missing from users table"

    product_cols = {c["name"] for c in inspector.get_columns("products")}
    for col in ("id", "barcode", "product_name", "brand", "ingredients", "additives"):
        assert col in product_cols, f"Column {col} missing from products table"

    food_log_cols = {c["name"] for c in inspector.get_columns("daily_food_log")}
    for col in ("id", "user_id", "barcode", "product_name", "calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"):
        assert col in food_log_cols, f"Column {col} missing from daily_food_log table"


def test_user_and_auth_flow():
    """3. Verify user and auth flow works with new profile fields."""
    with SessionLocal() as db:
        test_email = f"auth_test_{datetime.now().timestamp()}@example.com"
        pwd = "testpassword123"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed) is True
        assert verify_password("wrongpassword", hashed) is False

        user = User(
            name="Test User",
            email=test_email,
            hashed_password=hashed,
            age=25,
            weight=70.5,
            height=175.0,
            daily_calorie_limit=2200,
            diet_type="vegetarian",
            goal_type="maintenance",
            goal_target_days=30,
            created_at=db_service._utc_now_str(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        assert user.id is not None
        assert user.age == 25
        assert user.weight == 70.5
        assert user.height == 175.0

        token = create_access_token(user_id=int(user.id))
        extracted_id = verify_access_token(token)
        assert extracted_id == int(user.id)

        profile = db_service.get_user_profile(db, user_id=int(user.id))
        assert profile is not None
        assert profile["name"] == "Test User"
        assert profile["age"] == 25
        assert profile["weight"] == 70.5
        assert profile["height"] == 175.0


def test_scan_does_not_consume_food():
    """4. SCAN != EAT: Barcode lookup must NOT create a FoodLog consumption entry."""
    with SessionLocal() as db:
        # Create a test product
        test_barcode = f"9999{int(datetime.now().timestamp()) % 100000000:08d}"
        p = db_service.create_product(
            db,
            {
                "barcode": test_barcode,
                "product_name": "Test Snack Bar",
                "brand": "TestBrand",
            },
        )
        db_service.create_nutrition(
            db,
            {
                "product_id": p.id,
                "calories": 250.0,
                "fat": 10.0,
                "sugar": 5.0,
                "salt": 0.5,
                "protein": 8.0,
                "fiber": 4.0,
                "carbs": 30.0,
            },
        )
        db.commit()

        initial_logs_count = db.query(FoodLog).filter(FoodLog.barcode == test_barcode).count()
        initial_scans_count = db.query(ScanHistory).filter(ScanHistory.barcode == test_barcode).count()

        # Perform barcode lookup
        result = lookup_product(db, test_barcode, user_id=1)
        assert result is not None
        assert result["product_name"] == "Test Snack Bar"

        # Verify scan was recorded
        after_scans_count = db.query(ScanHistory).filter(ScanHistory.barcode == test_barcode).count()
        assert after_scans_count == initial_scans_count + 1

        # Verify NO food log consumption was created!
        after_logs_count = db.query(FoodLog).filter(FoodLog.barcode == test_barcode).count()
        assert after_logs_count == initial_logs_count, "Barcode scan automatically logged food consumption!"


def test_explicit_food_log():
    """5. Verify food is logged only when explicit food consumption is called."""
    with SessionLocal() as db:
        initial_count = db.query(FoodLog).filter(FoodLog.user_id == 1).count()
        db_service.log_food_consumption(
            db,
            barcode="manual",
            product_name="Explicit Apple",
            calories=95.0,
            fat=0.3,
            sugar=19.0,
            salt=0.01,
            protein=0.5,
            fiber=4.4,
            carbs=25.0,
            user_id=1,
        )
        db.commit()
        after_count = db.query(FoodLog).filter(FoodLog.user_id == 1).count()
        assert after_count == initial_count + 1


def test_weekly_report_retains_manual_and_ocr_logs():
    """6. Weekly report must retain manual and OCR food logs without linked products."""
    with SessionLocal() as db:
        unique_name = f"OCR Salty Soup {datetime.now().timestamp()}"
        db_service.log_food_consumption(
            db,
            barcode="manual",
            product_name=unique_name,
            calories=180.0,
            fat=5.0,
            sugar=2.0,
            salt=1.8,
            protein=6.0,
            fiber=1.0,
            carbs=12.0,
            user_id=1,
        )
        db.commit()

        week_logs = db_service.get_week_food_logs(db, user_id=1)
        assert isinstance(week_logs, dict)
        all_week_entries = [entry for day_entries in week_logs.values() for entry in day_entries]
        matched = [e for e in all_week_entries if e.get("product_name") == unique_name]
        assert len(matched) >= 1, "Manual/OCR entry disappeared from weekly food logs!"
        assert matched[0]["calories"] == 180.0
        assert matched[0]["salt"] == 1.8


def test_salt_threshold_consistency():
    """7. Salt threshold must be standardized at 1.5g across food_health_score and decision_explainer."""
    high_salt_product = {
        "calories": 100,
        "sugar": 5,
        "salt": 1.6,
        "fat": 5,
    }
    low_salt_product = {
        "calories": 100,
        "sugar": 5,
        "salt": 1.4,
        "fat": 5,
    }

    # Food health score
    score_high = compute_food_health_score(high_salt_product)
    score_low = compute_food_health_score(low_salt_product)
    assert "High salt" in score_high["reason"]
    assert "High salt" not in score_low["reason"]

    # Decision explainer
    reasons_high = build_decision_reasons(high_salt_product, remaining_calories=2000)
    reasons_low = build_decision_reasons(low_salt_product, remaining_calories=2000)
    assert "high sodium level" in reasons_high
    assert "high sodium level" not in reasons_low


def test_low_sodium_penalty_tripled():
    """Verify that low-sodium diet correctly triples the salt penalty (1x base + 2x diet = 3x total)."""
    # For salt = 1.2g: _salt_penalty is -20.
    product = {"calories": 100, "sugar": 0, "salt": 1.2, "fat": 0}
    base_res = compute_food_health_score(product)
    diet_res = compute_diet_aware_score(product, diet_type="low_sodium")
    # Base had score = 100 - 20 = 80
    # Diet score should have additional 2 * (-20) = -40, making it 80 - 40 = 40
    assert base_res["health_score"] == 80
    assert diet_res["health_score"] == 40
    assert diet_res["diet_note"] == "Salt penalty tripled for low-sodium diet"


def test_ingredient_analyzer_false_positives():
    """8. Ingredient analyzer must eliminate substring false positives and span duplicates."""
    # "msg" inside "message" must NOT match
    res_msg = analyze_ingredients("This package contains a friendly message to consumers.")
    assert "msg" not in res_msg["flags"]
    assert res_msg["risk_level"] == "LOW"

    # "msg" as a standalone word/token MUST match
    res_msg_true = analyze_ingredients("flavour enhancer (msg), iodized salt")
    assert "msg" in res_msg_true["flags"]

    # "high fructose corn syrup" must NOT duplicate "corn syrup"
    res_hfcs = analyze_ingredients("Ingredients: wheat flour, high fructose corn syrup, water")
    assert "high fructose corn syrup" in res_hfcs["flags"]
    assert "high fructose corn syrup" in res_hfcs["high_risk_flags"]
    assert "corn syrup" not in res_hfcs["flags"], "corn syrup incorrectly duplicated inside high fructose corn syrup"

    # "corn syrup" alone MUST match
    res_cs = analyze_ingredients("Ingredients: wheat flour, corn syrup, water")
    assert "corn syrup" in res_cs["flags"]
    assert "high fructose corn syrup" not in res_cs["flags"]


def test_fastapi_starts_and_health_responds():
    """Verify FastAPI application starts and handles HTTP requests."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "running"}
