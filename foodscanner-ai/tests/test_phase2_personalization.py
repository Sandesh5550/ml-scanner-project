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
from database.models import User
from services.auth_service import hash_password, create_access_token
from services.allergen_service import detect_allergens, check_allergen_warnings
from services.personalization import calculate_personalized_calorie_estimate, get_personalized_analysis
from services.food_health_score import compute_diet_aware_score
from api.main import app

client = TestClient(app)


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
def phase2_user(db_session):
    email = "phase2_personalization_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Phase 2 Tester",
            hashed_password=hash_password("Password123!"),
            diet_type="vegetarian",
            daily_calorie_limit=2000,
            created_at="2026-10-18 00:00:00",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=user.id)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


def test_allergen_detection_accuracy():
    """Verify allergen detection across diverse ingredient listings."""
    # Dairy
    detected_dairy = detect_allergens("Refined wheat flour, skimmed milk powder, whey protein, salt")
    assert "milk" in detected_dairy
    assert "wheat_gluten" in detected_dairy

    # Paneer / Ghee
    detected_indian_dairy = detect_allergens("Basmati rice, cow ghee, fresh paneer, spices")
    assert "milk" in detected_indian_dairy

    # Tree nuts & Peanuts
    detected_nuts = detect_allergens("Roasted peanuts, almonds, cashew nuts, honey, sea salt")
    assert "peanuts" in detected_nuts
    assert "tree_nuts" in detected_nuts

    # Clean / Allergen-free food
    detected_clean = detect_allergens("Fresh organic apples, filtered water, lemon juice")
    assert len(detected_clean) == 0


def test_user_allergen_matching_and_warnings():
    """Verify critical safety warnings are generated when product matches user declared allergies."""
    detected = ["peanuts", "milk"]

    # 1. User has declared peanut allergy -> MUST be CRITICAL
    warnings_critical = check_allergen_warnings(detected, user_allergens="peanuts")
    assert len(warnings_critical) == 1
    assert warnings_critical[0]["allergen"] == "peanuts"
    assert warnings_critical[0]["severity"] == "CRITICAL"
    assert warnings_critical[0]["user_match"] is True
    assert "Exercise extreme caution" in warnings_critical[0]["warning"]

    # 2. User has multiple allergies (comma separated or list)
    warnings_multi = check_allergen_warnings(detected, user_allergens=["peanuts", "milk", "soy"])
    assert len(warnings_multi) == 2
    match_allergens = {w["allergen"] for w in warnings_multi}
    assert match_allergens == {"peanuts", "milk"}
    for w in warnings_multi:
        assert w["severity"] == "CRITICAL"
        assert w["user_match"] is True

    # 3. User with no declared allergies receives informational notice
    warnings_info = check_allergen_warnings(detected, user_allergens=None)
    assert len(warnings_info) == 2
    for w in warnings_info:
        assert w["user_match"] is False


def test_mifflin_st_jeor_calorie_estimator():
    """Verify Mifflin-St Jeor calculation with goal adjustments and boundary validations."""
    # 28 yo, 70 kg, 175 cm
    # BMR: (10 * 70) + (6.25 * 175) - (5 * 28) - 78 = 700 + 1093.75 - 140 - 78 = 1575.75
    # TDEE: 1575.75 * 1.35 = 2127.26
    est_maint = calculate_personalized_calorie_estimate(age=28, weight_kg=70.0, height_cm=175.0, goal_type="maintenance")
    assert est_maint is not None
    assert est_maint["bmr"] == 1576.0
    assert est_maint["tdee"] == 2127.0
    assert est_maint["recommended_calories"] == 2150  # Rounded to nearest 50
    assert "disclaimer" in est_maint

    # Weight loss deficit
    est_loss = calculate_personalized_calorie_estimate(age=28, weight_kg=70.0, height_cm=175.0, goal_type="lose_weight")
    assert est_loss["recommended_calories"] < est_maint["recommended_calories"]
    assert "fat loss" in est_loss["goal_note"].lower()

    # Muscle gain surplus
    est_muscle = calculate_personalized_calorie_estimate(age=28, weight_kg=70.0, height_cm=175.0, goal_type="build_muscle")
    assert est_muscle["recommended_calories"] > est_maint["recommended_calories"]

    # Boundary validation: invalid or extreme inputs must return None safely
    assert calculate_personalized_calorie_estimate(age=-5, weight_kg=70, height_cm=175) is None
    assert calculate_personalized_calorie_estimate(age=28, weight_kg=-10, height_cm=175) is None
    assert calculate_personalized_calorie_estimate(age=150, weight_kg=70, height_cm=175) is None
    assert calculate_personalized_calorie_estimate(age=None, weight_kg=70, height_cm=175) is None


def test_diet_aware_score_strictness():
    """Verify enhanced dietary rules: vegan, diabetic, and low-sodium."""
    # Vegan product with paneer or whey
    prod_non_vegan = {
        "product_name": "Protein Bar",
        "ingredients": "Oats, whey protein isolate, dates, cocoa",
        "calories": 250,
        "sugar": 8,
        "fat": 5,
        "saturated_fat": 2,
        "salt": 0.2,
        "protein": 18,
    }
    vegan_score = compute_diet_aware_score(prod_non_vegan, diet_type="vegan")
    assert vegan_score["decision"] == "AVOID"
    assert "vegan" in vegan_score["diet_note"].lower()

    # Diabetic product with high sugar (> 15g)
    prod_high_sugar = {
        "product_name": "Sweet Cereal",
        "ingredients": "Corn, high fructose corn syrup, sugar, salt",
        "calories": 380,
        "sugar": 22.0,
        "fat": 2,
        "saturated_fat": 0.5,
        "salt": 0.8,
        "protein": 4,
    }
    diabetic_score = compute_diet_aware_score(prod_high_sugar, diet_type="diabetic")
    assert diabetic_score["decision"] == "AVOID"
    assert "diabetic" in diabetic_score["diet_note"].lower()


def test_user_profile_biometrics_and_allergens_api_lifecycle(phase2_user):
    """Verify full end-to-end user profile update and retrieval with allergens and biometrics."""
    headers = phase2_user["headers"]

    update_payload = {
        "daily_calorie_limit": 2100,
        "diet_type": "vegetarian",
        "allergens": "peanuts,tree_nuts,shellfish",
        "age": 30,
        "weight": 72.5,
        "height": 178.0,
        "goal_type": "lose_weight",
        "goal_target_days": 60,
    }

    # 1. Update Profile
    put_res = client.put("/user/profile", json=update_payload, headers=headers)
    assert put_res.status_code == 200, put_res.text
    put_data = put_res.json()
    assert put_data["allergens"] == "peanuts,tree_nuts,shellfish"
    assert put_data["age"] == 30
    assert put_data["weight"] == 72.5
    assert put_data["height"] == 178.0
    assert put_data["calorie_estimate"] is not None
    assert put_data["calorie_estimate"]["recommended_calories"] > 0

    # 2. Get Profile
    get_res = client.get("/user/profile", headers=headers)
    assert get_res.status_code == 200, get_res.text
    get_data = get_res.json()
    assert get_data["allergens"] == "peanuts,tree_nuts,shellfish"
    assert get_data["age"] == 30
    assert get_data["weight"] == 72.5
    assert get_data["height"] == 178.0
    assert get_data["calorie_estimate"] is not None
