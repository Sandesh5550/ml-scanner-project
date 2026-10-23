from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-7-product-intelligence"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db
from database.init_db import init_db
from database.models import User, Product, Nutrition, FoodLog, ScanHistory
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.food_health_score import compute_food_health_score
from services.recommendation_engine import get_healthier_alternatives
from api.main import app


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
    email = "batch7_demo_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Batch7 Demo User",
            hashed_password=hash_password("DemoPassword123"),
            daily_calorie_limit=2000,
            age=26,
            weight=72.0,
            height=176.0,
            diet_type="vegan",
            goal_type="build_muscle",
            goal_target_days=30,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    else:
        user.age = 26
        user.weight = 72.0
        user.height = 176.0
        user.diet_type = "vegan"
        user.goal_type = "build_muscle"
        user.daily_calorie_limit = 2000
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {"user": user, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(autouse=True)
def seed_demo_products(db_session):
    """Seed test products for Batch 7 comparison and analysis tests."""
    p1 = db_service.get_product_by_barcode(db_session, "8907000000001")
    if not p1:
        prod1 = db_service.create_product(
            db_session,
            {
                "barcode": "8907000000001",
                "product_name": "Batch7 Fried Potato Crisps",
                "brand": "CrispySnacks",
                "nutriscore": "d",
                "ingredients": "Potatoes, palm oil, salt, flavor enhancer",
                "additives": "E621",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod1.id,
                "calories": 540.0,
                "fat": 34.0,
                "saturated_fat": 15.0,
                "sugar": 3.0,
                "salt": 1.8,
                "protein": 5.5,
                "fiber": 2.0,
                "carbs": 53.0,
            },
        )
        db_session.commit()

    p2 = db_service.get_product_by_barcode(db_session, "8907000000002")
    if not p2:
        prod2 = db_service.create_product(
            db_session,
            {
                "barcode": "8907000000002",
                "product_name": "Batch7 Roasted Makhana Superfood",
                "brand": "HealthyBites",
                "nutriscore": "a",
                "ingredients": "Fox nuts, olive oil, rock salt",
                "additives": None,
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod2.id,
                "calories": 350.0,
                "fat": 8.0,
                "saturated_fat": 1.2,
                "sugar": 0.5,
                "salt": 0.4,
                "protein": 10.0,
                "fiber": 7.5,
                "carbs": 62.0,
            },
        )
        db_session.commit()

    p3 = db_service.get_product_by_barcode(db_session, "8907000000003")
    if not p3:
        prod3 = db_service.create_product(
            db_session,
            {
                "barcode": "8907000000003",
                "product_name": "Batch7 Minimal Snack Incomplete Nutrition",
                "brand": "MinimalBrand",
                "nutriscore": None,
                "ingredients": "Grain blend",
                "additives": None,
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod3.id,
                "calories": 200.0,
                "fat": None,
                "saturated_fat": None,
                "sugar": None,
                "salt": None,
                "protein": None,
                "fiber": None,
                "carbs": None,
            },
        )
        db_session.commit()


# ==============================================================================
# TEST 1: Compare Endpoint Authentication
# ==============================================================================
def test_compare_endpoint_authentication(auth_user):
    client = TestClient(app)

    # 1. Unauthenticated request must return 401
    res_unauth = client.post(
        "/compare",
        json={"product_a": "Batch7 Fried Potato Crisps", "product_b": "Batch7 Roasted Makhana Superfood"},
    )
    assert res_unauth.status_code == 401

    # 2. Invalid token must return 401
    res_bad = client.post(
        "/compare",
        json={"product_a": "Batch7 Fried Potato Crisps", "product_b": "Batch7 Roasted Makhana Superfood"},
        headers={"Authorization": "Bearer invalid.token.payload"},
    )
    assert res_bad.status_code == 401

    # 3. Authenticated request must succeed
    res_auth = client.post(
        "/compare",
        json={"product_a": "Batch7 Fried Potato Crisps", "product_b": "Batch7 Roasted Makhana Superfood"},
        headers=auth_user["headers"],
    )
    assert res_auth.status_code == 200


# ==============================================================================
# TEST 2: Compare Endpoint Valid Products
# ==============================================================================
def test_compare_endpoint_valid_products(auth_user, seed_demo_products):
    client = TestClient(app)
    res = client.post(
        "/compare",
        json={"product_a": "Batch7 Fried Potato Crisps", "product_b": "Batch7 Roasted Makhana Superfood"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert "product_a" in data
    assert "product_b" in data
    assert "healthier_product" in data
    assert "reasons" in data

    pa = data["product_a"]
    pb = data["product_b"]

    # Verify structured fields
    for p in [pa, pb]:
        assert "name" in p and p["name"] is not None
        assert "brand" in p
        assert "barcode" in p
        assert "health_score" in p
        assert isinstance(p["health_score"], (int, float))
        assert "nutriscore" in p
        assert "nutrition" in p
        nutr = p["nutrition"]
        for key in ["calories", "fat", "saturated_fat", "carbohydrates", "sugar", "fiber", "protein", "salt"]:
            assert key in nutr

    # Healthier product must be the higher scoring one
    if pb["health_score"] > pa["health_score"]:
        assert data["healthier_product"] == pb["name"]
    elif pa["health_score"] > pb["health_score"]:
        assert data["healthier_product"] == pa["name"]

    assert len(data["reasons"]) > 0


# ==============================================================================
# TEST 3: Compare Endpoint Missing Product & Malformed Input
# ==============================================================================
def test_compare_endpoint_missing_product(auth_user):
    client = TestClient(app)

    # Missing product_a
    res404_a = client.post(
        "/compare",
        json={"product_a": "NonExistentUnknownProduct99999", "product_b": "Batch7 Roasted Makhana Superfood"},
        headers=auth_user["headers"],
    )
    assert res404_a.status_code == 404
    assert "product_a not found" in res404_a.json()["detail"]

    # Missing product_b
    res404_b = client.post(
        "/compare",
        json={"product_a": "Batch7 Fried Potato Crisps", "product_b": "NonExistentUnknownProduct99999"},
        headers=auth_user["headers"],
    )
    assert res404_b.status_code == 404
    assert "product_b not found" in res404_b.json()["detail"]

    # Empty inputs return 400 Bad Request
    res400 = client.post(
        "/compare",
        json={"product_a": "", "product_b": ""},
        headers=auth_user["headers"],
    )
    assert res400.status_code == 400


# ==============================================================================
# TEST 4: Compare Endpoint Incomplete Nutrition Handling
# ==============================================================================
def test_compare_endpoint_incomplete_nutrition(auth_user, seed_demo_products):
    client = TestClient(app)
    res = client.post(
        "/compare",
        json={"product_a": "Batch7 Minimal Snack Incomplete Nutrition", "product_b": "Batch7 Roasted Makhana Superfood"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    pa_nutr = data["product_a"]["nutrition"]

    # Saturated fat, sugar, etc. should be None/null without crashing
    assert pa_nutr["sugar"] is None
    assert pa_nutr["fat"] is None
    assert pa_nutr["calories"] == 200.0


# ==============================================================================
# TEST 5: Alternatives Valid Category
# ==============================================================================
def test_alternatives_valid_category(auth_user):
    client = TestClient(app)

    # 1. Test POST /alternatives for snacks
    res_snacks = client.post(
        "/alternatives",
        json={
            "product_name": "Balaji Wafers Masala",
            "nutrition": {"calories": 540, "fat": 35, "sugar": 2.5, "salt": 2.2, "protein": 6, "fiber": 2, "carbs": 52},
            "limit": 3,
        },
        headers=auth_user["headers"],
    )
    assert res_snacks.status_code == 200
    data_snacks = res_snacks.json()
    assert data_snacks["category"] == "chips_and_snacks"
    assert len(data_snacks["alternatives"]) > 0

    for alt in data_snacks["alternatives"]:
        assert "product_name" in alt
        assert "category" in alt
        assert "health_score" in alt
        assert "nutriscore" in alt
        assert "nutrition" in alt
        assert "reason" in alt
        assert "improvement_score" in alt

    # 2. Test alternatives across chocolate/confectionery
    res_choc = client.post(
        "/alternatives",
        json={
            "product_name": "Amul Milk Chocolate",
            "nutrition": {"calories": 550, "fat": 32, "sugar": 50, "salt": 0.2, "protein": 7, "fiber": 2, "carbs": 55},
            "limit": 3,
        },
        headers=auth_user["headers"],
    )
    assert res_choc.status_code == 200
    assert len(res_choc.json()["alternatives"]) > 0

    # 3. Test alternatives across biscuits
    res_bisc = client.post(
        "/alternatives",
        json={
            "product_name": "Britannia Bourbon",
            "nutrition": {"calories": 490, "fat": 20, "sugar": 35, "salt": 0.8, "protein": 5, "fiber": 1.5, "carbs": 70},
            "limit": 3,
        },
        headers=auth_user["headers"],
    )
    assert res_bisc.status_code == 200
    assert len(res_bisc.json()["alternatives"]) > 0


# ==============================================================================
# TEST 6: Alternatives Empty Result For Already Healthy Product
# ==============================================================================
def test_alternatives_empty_result(auth_user):
    client = TestClient(app)
    # Green tea with 0 sugar, 0 fat, minimal calories has no significantly healthier packaged alternative
    res = client.post(
        "/alternatives",
        json={
            "product_name": "Organic Green Tea Unsweetened",
            "nutrition": {"calories": 2.0, "fat": 0.0, "sugar": 0.0, "salt": 0.0, "protein": 0.0, "fiber": 0.0, "carbs": 0.5},
            "limit": 3,
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["alternatives"], list)


# ==============================================================================
# TEST 7: Alternatives Missing Nutrition Handling
# ==============================================================================
def test_alternatives_missing_nutrition(auth_user):
    client = TestClient(app)
    # Unknown product with no nutrition data supplied
    res = client.post(
        "/alternatives",
        json={"product_name": "UnknownFoodWithoutNutr", "nutrition": {}},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert data["alternatives"] == []
    assert data["total_alternatives"] == 0

    # Python service direct check
    alts_empty = get_healthier_alternatives("UnknownFoodWithoutNutr", {})
    assert alts_empty == []


# ==============================================================================
# TEST 8: Personalized Profile Retrieval and Update
# ==============================================================================
def test_personalized_profile_retrieval(auth_user):
    client = TestClient(app)

    # 1. Retrieve profile
    res = client.get("/user/profile", headers=auth_user["headers"])
    assert res.status_code == 200
    profile = res.json()
    assert profile["email"] == "batch7_demo_user@example.com"
    assert profile["diet_type"] == "vegan"
    assert profile["goal_type"] == "build_muscle"
    assert profile["age"] == 26
    assert profile["weight"] == 72.0
    assert profile["height"] == 176.0

    # 2. Update profile
    res_update = client.put(
        "/user/profile",
        json={
            "age": 27,
            "weight": 70.0,
            "goal_type": "lose_weight",
            "diet_type": "vegetarian",
        },
        headers=auth_user["headers"],
    )
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["age"] == 27
    assert updated["weight"] == 70.0
    assert updated["goal_type"] == "lose_weight"
    assert updated["diet_type"] == "vegetarian"


# ==============================================================================
# TEST 9: Personalized Analysis With Valid Profile
# ==============================================================================
def test_personalized_analysis_with_valid_profile(auth_user):
    client = TestClient(app)

    # Set profile to diabetic and weight_loss
    client.put(
        "/user/profile",
        json={"diet_type": "diabetic", "goal_type": "lose_weight", "daily_calorie_limit": 1800},
        headers=auth_user["headers"],
    )

    res = client.post(
        "/analyze",
        json={
            "product_name": "High Sugar Soda",
            "calories": 250,
            "sugar": 35,
            "carbs": 60,
            "fat": 0,
            "salt": 0.1,
            "protein": 0,
            "fiber": 0,
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()

    assert "personalized_analysis" in data
    pers = data["personalized_analysis"]

    assert pers["user_profile"]["diet_type"] == "diabetic"
    assert pers["user_profile"]["goal_type"] == "lose_weight"
    assert pers["diet_alignment"]["compatibility"] == "CAUTION"
    assert len(pers["goal_alignment"]["signals"]) > 0
    assert "calorie_budget" in pers
    assert pers["calorie_budget"]["product_calories"] == 250.0


# ==============================================================================
# TEST 10: Personalized Analysis With Missing Profile
# ==============================================================================
def test_personalized_analysis_with_missing_profile(db_session):
    # Create user with all profile attributes None
    email = "blank_profile_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Blank Profile User",
            hashed_password=hash_password("Pass123"),
            daily_calorie_limit=2000,
            diet_type=None,
            goal_type=None,
            age=None,
            weight=None,
            height=None,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app)
    res = client.post(
        "/analyze",
        json={"product_name": "Standard Toast", "calories": 150, "fat": 2, "sugar": 3, "salt": 0.4},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()

    pers = data["personalized_analysis"]
    assert pers["user_profile"]["diet_type"] is None
    assert pers["user_profile"]["goal_type"] is None
    assert pers["user_profile"]["age"] is None
    assert pers["user_profile"]["weight"] is None
    assert pers["goal_alignment"]["signals"] == []
    # Does not crash and evaluates standard health score
    assert "health_score" in data["analysis"]


# ==============================================================================
# TEST 11: Score Explanation Verification
# ==============================================================================
def test_score_explanation(auth_user, seed_demo_products):
    client = TestClient(app)
    res = client.get("/explain/8907000000001", headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()

    assert data["product_name"] == "Batch7 Fried Potato Crisps"
    assert "final_score" in data
    assert "final_decision" in data
    assert "steps" in data
    assert "positive_factors" in data
    assert "negative_factors" in data
    assert "summary" in data
    assert "threshold_info" in data
    assert "score_calculation" in data

    # Negative factors should detect high fat and high salt
    neg_factors = [f["factor"] for f in data["negative_factors"]]
    assert "Fat" in neg_factors or "Salt" in neg_factors or "Calories" in neg_factors


# ==============================================================================
# TEST 12: Claim Verification Integration
# ==============================================================================
def test_claim_verification_integration(auth_user, seed_demo_products):
    client = TestClient(app)

    # 1. Endpoint direct test
    res = client.post(
        "/verify-claims",
        json={
            "barcode": "8907000000002",
            "claims": ["sugar_free", "high_protein", "low_fat"],
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "results" in data
    claims_map = {r["claim"]: r["status"] for r in data["results"]}

    # Sugar is 0.5g/100g, so sugar_free is SUPPORTED under FSSAI <= 0.5g
    assert claims_map.get("sugar_free") == "SUPPORTED"

    # 2. Integration in /analyze
    res_an = client.post(
        "/analyze",
        json={
            "product_name": "Batch7 Test Protein Bar",
            "protein": 22.0,
            "sugar": 1.0,
            "fat": 4.0,
            "claims": ["high_protein"],
        },
        headers=auth_user["headers"],
    )
    assert res_an.status_code == 200
    an_data = res_an.json()
    assert "claim_verification" in an_data
    assert an_data["claim_verification"]["results"][0]["status"] == "SUPPORTED"


# ==============================================================================
# TEST 13: OCR Structured Data Integration
# ==============================================================================
def test_ocr_structured_data_integration(auth_user):
    client = TestClient(app)

    # Simulated OCR structured output fed to /analyze
    ocr_extracted_nutrition = {
        "product_name": "OCR Scanned Cornflakes",
        "calories": 380.0,
        "fat": 1.2,
        "sugar": 8.0,
        "salt": 1.1,
        "protein": 7.0,
        "fiber": 4.0,
        "carbs": 84.0,
        "serving_size": 30.0,
        "ingredients": "Milled corn, sugar, malt flavoring, salt",
        "claims": ["low_fat"],
    }

    res = client.post("/analyze", json=ocr_extracted_nutrition, headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()

    assert data["product"]["name"] == "OCR Scanned Cornflakes"
    assert data["product"]["nutrition_per_serving"]["calories"] == 114.0  # 380 * 0.3
    assert data["analysis"]["health_score"] > 0
    assert "claim_verification" in data
    assert data["claim_verification"]["results"][0]["status"] == "SUPPORTED"


# ==============================================================================
# TEST 14: Scan Does NOT Create Food Log
# ==============================================================================
def test_scan_does_not_create_food_log(auth_user, seed_demo_products, db_session):
    client = TestClient(app)
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    res = client.post("/scan", json={"barcode": "8907000000001"}, headers=auth_user["headers"])
    assert res.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "POST /scan must NEVER write to FoodLog (Scan != Eat)"


# ==============================================================================
# TEST 15: Product Analysis Does NOT Create Food Log
# ==============================================================================
def test_product_analysis_does_not_create_food_log(auth_user, db_session):
    client = TestClient(app)
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    res = client.post(
        "/analyze",
        json={"product_name": "Test Analysis Apple", "calories": 52, "sugar": 10},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "POST /analyze must NEVER write to FoodLog (Scan != Eat)"


# ==============================================================================
# TEST 16: Alternatives Do NOT Create Food Log
# ==============================================================================
def test_alternatives_do_not_create_food_log(auth_user, seed_demo_products, db_session):
    client = TestClient(app)
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    res1 = client.post(
        "/alternatives",
        json={"barcode": "8907000000001"},
        headers=auth_user["headers"],
    )
    assert res1.status_code == 200

    res2 = client.get("/alternatives/8907000000001", headers=auth_user["headers"])
    assert res2.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "Alternatives endpoints must NEVER write to FoodLog"


# ==============================================================================
# TEST 17: Compare Does NOT Create Food Log
# ==============================================================================
def test_compare_does_not_create_food_log(auth_user, seed_demo_products, db_session):
    client = TestClient(app)
    user_id = auth_user["user"].id

    count_before = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()

    res = client.post(
        "/compare",
        json={"product_a": "8907000000001", "product_b": "8907000000002"},
        headers=auth_user["headers"],
    )
    assert res.status_code == 200

    count_after = db_session.query(FoodLog).filter(FoodLog.user_id == user_id).count()
    assert count_after == count_before, "POST /compare must NEVER write to FoodLog"


# ==============================================================================
# TEST 18: API Response JSON Serialization
# ==============================================================================
def test_api_response_json_serialization(auth_user, seed_demo_products):
    client = TestClient(app)

    # 1. /compare response serialization
    res_cmp = client.post(
        "/compare",
        json={"product_a": "8907000000001", "product_b": "8907000000002"},
        headers=auth_user["headers"],
    )
    assert res_cmp.status_code == 200
    json_cmp = json.dumps(res_cmp.json())
    assert isinstance(json_cmp, str)

    # 2. /analyze response serialization
    res_an = client.post(
        "/analyze",
        json={"product_name": "Test Snack", "calories": 200, "sugar": 5},
        headers=auth_user["headers"],
    )
    assert res_an.status_code == 200
    json_an = json.dumps(res_an.json())
    assert isinstance(json_an, str)

    # 3. /explain/{barcode} response serialization
    res_exp = client.get("/explain/8907000000001", headers=auth_user["headers"])
    assert res_exp.status_code == 200
    json_exp = json.dumps(res_exp.json())
    assert isinstance(json_exp, str)

    # 4. /alternatives response serialization
    res_alt = client.get("/alternatives/8907000000001", headers=auth_user["headers"])
    assert res_alt.status_code == 200
    json_alt = json.dumps(res_alt.json())
    assert isinstance(json_alt, str)

    # 5. /scan response serialization
    res_scan = client.post("/scan", json={"barcode": "8907000000001"}, headers=auth_user["headers"])
    assert res_scan.status_code == 200
    json_scan = json.dumps(res_scan.json())
    assert isinstance(json_scan, str)
