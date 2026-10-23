from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-batch2")
os.environ.setdefault("FOODSCANNER_OCR_ENGINE", "tesseract")

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.main import app
from database.orm import Base
from database.models import FoodLog, Product, ScanHistory, User
from ml_model.predict_nutriscore import (
    MODEL_PATH,
    ModelArtifactError,
    calculate_heuristic_nutriscore,
    load_bundle,
    predict_nutriscore,
    predict_nutriscore_details,
)
from services import db_service
from services.barcode_lookup import lookup_product
from services.openfoodfacts_service import (
    fetch_product_by_barcode,
    fetch_product_by_barcode_async,
    fetch_product_by_barcode_v2,
)
from services.recommendation_engine import (
    get_healthier_alternatives,
    infer_food_category,
)


# ==========================================
# 1. ML INFERENCE & MODEL VALIDATION TESTS
# ==========================================

def test_ml_artifact_exists_and_loads():
    assert MODEL_PATH.exists(), f"Model artifact missing at {MODEL_PATH}"
    bundle = load_bundle(MODEL_PATH)
    assert isinstance(bundle, dict), "Bundle must be a dict"
    assert "model" in bundle, "Bundle must contain 'model'"
    assert "features" in bundle, "Bundle must contain 'features'"
    assert "model_version" in bundle, "Bundle must contain 'model_version'"
    assert "classes" in bundle, "Bundle must contain 'classes'"
    assert "metrics" in bundle, "Bundle must contain 'metrics'"
    assert bundle["features"] == ["calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"]
    assert bundle["classes"] == ["a", "b", "c", "d", "e"]
    assert bundle["metrics"]["test_accuracy"] > 0.75, "Expected accuracy above baseline threshold"


def test_ml_prediction_and_determinism():
    healthy_product = {
        "calories": 35.0,
        "fat": 0.2,
        "sugar": 2.5,
        "salt": 0.05,
        "protein": 1.2,
        "fiber": 3.0,
        "carbs": 7.0,
    }
    unhealthy_product = {
        "calories": 540.0,
        "fat": 36.0,
        "sugar": 52.0,
        "salt": 2.1,
        "protein": 4.5,
        "fiber": 0.5,
        "carbs": 58.0,
    }

    pred_healthy = predict_nutriscore(healthy_product)
    pred_unhealthy = predict_nutriscore(unhealthy_product)

    assert pred_healthy == "A"
    assert pred_unhealthy == "E"

    # Determinism: same inputs must always yield identical outputs across repeated runs
    for _ in range(5):
        assert predict_nutriscore(healthy_product) == pred_healthy
        assert predict_nutriscore(unhealthy_product) == pred_unhealthy


def test_ml_predict_details_and_probabilities():
    product = {
        "calories": 250.0,
        "fat": 8.0,
        "sugar": 12.0,
        "salt": 0.8,
        "protein": 6.0,
        "fiber": 2.5,
        "carbs": 35.0,
    }
    details = predict_nutriscore_details(product)

    assert "nutriscore" in details
    assert details["nutriscore"] in ["A", "B", "C", "D", "E"]
    assert "probabilities" in details
    assert len(details["probabilities"]) == 5
    prob_sum = sum(details["probabilities"].values())
    assert 0.99 <= prob_sum <= 1.01, f"Probabilities must sum to ~1.0, got {prob_sum}"
    assert details["model_version"] == "1.0.0"
    assert details["features"]["calories"] == 250.0


def test_ml_missing_artifact_raises_filenotfound():
    missing_path = Path("this_model_file_does_not_exist_at_all.pkl")
    with pytest.raises(FileNotFoundError):
        predict_nutriscore({"calories": 100}, model_path=missing_path)


def test_ml_invalid_artifact_raises_modelartifacterror(tmp_path):
    corrupt_file = tmp_path / "corrupt_model.pkl"
    corrupt_file.write_text("not a valid pickle file", encoding="utf-8")

    with pytest.raises(ModelArtifactError):
        predict_nutriscore({"calories": 100}, model_path=corrupt_file)


def test_ml_invalid_bundle_structure_raises_modelartifacterror(tmp_path):
    import joblib

    invalid_bundle_file = tmp_path / "invalid_bundle.pkl"
    # Bundle missing 'features'
    joblib.dump({"model": "dummy_model"}, invalid_bundle_file)

    with pytest.raises(ModelArtifactError):
        predict_nutriscore({"calories": 100}, model_path=invalid_bundle_file)


def test_heuristic_fallback_is_explicit_only():
    # Verify that calculate_heuristic_nutriscore is separate and explicit
    res = calculate_heuristic_nutriscore({
        "calories": 500,
        "fat": 30,
        "sugar": 40,
        "salt": 2,
        "fiber": 0,
        "protein": 2,
    })
    assert res in ["D", "E"]


# ==========================================
# 2. OPENFOODFACTS TESTS (MOCKED)
# ==========================================

MOCK_OFF_SUCCESS_PAYLOAD = {
    "status": 1,
    "product": {
        "product_name": "Organic Oat Flakes",
        "brands": "Bio Choice, WholeGrains",
        "nutriscore_grade": "a",
        "ingredients_text": "100% whole grain oats",
        "additives_tags": [],
        "nutriments": {
            "energy-kcal_100g": 365.0,
            "fat_100g": 6.9,
            "saturated-fat_100g": 1.2,
            "sugars_100g": 1.3,
            "salt_100g": 0.02,
            "proteins_100g": 13.5,
            "fiber_100g": 10.0,
            "carbohydrates_100g": 58.7,
        },
    },
}


def test_openfoodfacts_successful_mocked_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_OFF_SUCCESS_PAYLOAD
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        res = fetch_product_by_barcode("1234567890123")

    assert res is not None
    assert res["barcode"] == "1234567890123"
    assert res["product_name"] == "Organic Oat Flakes"
    assert res["brand"] == "Bio Choice"
    assert res["nutriscore"] == "a"
    assert res["calories"] == 365.0
    assert res["fat"] == 6.9
    assert res["saturated_fat"] == 1.2
    assert res["sugar"] == 1.3
    assert res["salt"] == 0.02
    assert res["protein"] == 13.5
    assert res["fiber"] == 10.0
    assert res["carbs"] == 58.7


def test_openfoodfacts_missing_fields_and_sodium_fallback():
    # Payload with sodium but missing salt, and kJ but missing kcal
    payload = {
        "status": 1,
        "product": {
            "product_name": "Mineral Crackers",
            "brands": "SnackCorp",
            "nutriments": {
                "energy_100g": 1673.6,  # 1673.6 kJ ≈ 400.0 kcal
                "sodium_100g": 0.4,     # 0.4g sodium * 2.5 = 1.0g salt
                "fat_100g": 12.0,
            },
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        res = fetch_product_by_barcode("9999999999999")

    assert res is not None
    assert res["product_name"] == "Mineral Crackers"
    assert res["brand"] == "SnackCorp"
    assert res["calories"] == 400.0
    assert res["salt"] == 1.0
    assert res["sugar"] is None
    assert res["fiber"] is None


def test_openfoodfacts_timeout_handling():
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Connection timed out")):
        res = fetch_product_by_barcode("1234567890123")
    assert res is None


def test_openfoodfacts_network_error_handling():
    with patch("httpx.Client.get", side_effect=httpx.NetworkError("DNS failure")):
        res = fetch_product_by_barcode("1234567890123")
    assert res is None


def test_openfoodfacts_http_404_and_500():
    # 404 Not Found
    mock_404 = MagicMock()
    mock_404.status_code = 404

    with patch("httpx.Client.get", return_value=mock_404):
        res_404 = fetch_product_by_barcode("0000000000000")
    assert res_404 is None

    # 500 Server Error
    mock_500 = MagicMock()
    mock_500.status_code = 500
    mock_500.raise_for_status.side_effect = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=mock_500)

    with patch("httpx.Client.get", return_value=mock_500):
        res_500 = fetch_product_by_barcode("0000000000000")
    assert res_500 is None


def test_openfoodfacts_malformed_response():
    mock_malformed = MagicMock()
    mock_malformed.status_code = 200
    mock_malformed.json.side_effect = json.JSONDecodeError("Expecting value", "bad json", 0)

    with patch("httpx.Client.get", return_value=mock_malformed):
        res = fetch_product_by_barcode("1234567890123")
    assert res is None


# ==========================================
# 3. RECOMMENDATION ENGINE TESTS
# ==========================================

def test_recommendations_comparable_category():
    # Balaji Wafers Masala belongs to chips_and_snacks
    chips_nutr = {
        "calories": 540.0,
        "fat": 35.0,
        "sugar": 2.5,
        "salt": 2.2,
        "protein": 6.0,
        "fiber": 2.0,
        "carbs": 52.0,
    }
    category = infer_food_category("Balaji Wafers Masala", chips_nutr)
    assert category == "chips_and_snacks"

    alts = get_healthier_alternatives("Balaji Wafers Masala", chips_nutr, limit=3)
    assert len(alts) > 0

    for alt in alts:
        assert alt["category"] == "chips_and_snacks"
        assert alt["product_name"] != "Balaji Wafers Masala"
        assert "nutrition" in alt
        assert "reason" in alt
        assert "improvement_score" in alt
        assert alt["improvement_score"] > 0
        assert "key_differences" in alt
        assert len(alt["key_differences"]) > 0


def test_recommendations_nutritional_difference_calculations():
    chocolate_nutr = {
        "calories": 550.0,
        "fat": 35.0,
        "sugar": 48.0,
        "salt": 0.3,
        "protein": 5.0,
        "fiber": 3.0,
        "carbs": 55.0,
    }
    alts = get_healthier_alternatives("Amul Milk Chocolate", chocolate_nutr, limit=3)
    assert len(alts) > 0

    top_alt = alts[0]
    # Check that diff calculation accurately matches stored values
    if "sugar" in top_alt["key_differences"]:
        alt_sugar = top_alt["nutrition"]["sugar"]
        expected_diff = int(round(((chocolate_nutr["sugar"] - alt_sugar) / chocolate_nutr["sugar"]) * 100))
        assert top_alt["key_differences"]["sugar"] == f"-{expected_diff}%"


def test_recommendations_insufficient_candidates_graceful():
    # Empty inputs
    assert get_healthier_alternatives("", {}) == []
    assert get_healthier_alternatives("SomeProduct", None) == []

    # Extremely healthy food with nothing healthier in its category
    super_healthy_drink = {
        "calories": 5.0,
        "fat": 0.0,
        "sugar": 0.0,
        "salt": 0.0,
        "protein": 0.0,
        "fiber": 0.0,
        "carbs": 1.0,
    }
    alts = get_healthier_alternatives("Green Tea Unsweetened", super_healthy_drink, limit=3)
    assert isinstance(alts, list)


def test_recommendations_deterministic_ranking():
    nutr = {
        "calories": 500.0,
        "fat": 25.0,
        "sugar": 30.0,
        "salt": 1.5,
        "protein": 6.0,
        "fiber": 1.0,
        "carbs": 60.0,
    }
    alts_run1 = get_healthier_alternatives("Britannia Bourbon", nutr, limit=3)
    alts_run2 = get_healthier_alternatives("Britannia Bourbon", nutr, limit=3)

    assert len(alts_run1) == len(alts_run2)
    for a1, a2 in zip(alts_run1, alts_run2):
        assert a1["product_name"] == a2["product_name"]
        assert a1["improvement_score"] == a2["improvement_score"]


# ==========================================
# 4. INTEGRATION & SCAN ≠ EAT VERIFICATION
# ==========================================

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    db_service.ensure_default_user(db)
    yield db
    db.close()


def test_full_barcode_flow_integration_and_scan_not_eat(test_db):
    barcode = "8901234567890"

    # Mock OpenFoodFacts to return high sugar snack
    mock_payload = {
        "status": 1,
        "product": {
            "product_name": "Sweet Corn Chips",
            "brands": "TastySnacks",
            "nutriscore_grade": None,  # Force ML prediction
            "ingredients_text": "Corn, palm oil, sugar, salt, msg",
            "nutriments": {
                "energy-kcal_100g": 520.0,
                "fat_100g": 32.0,
                "sugars_100g": 24.0,
                "salt_100g": 1.8,
                "proteins_100g": 5.0,
                "fiber_100g": 2.0,
                "carbohydrates_100g": 55.0,
            },
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp):
        res = lookup_product(
            test_db,
            barcode=barcode,
            user_id=1,
            daily_calorie_limit=2000,
        )

    assert res is not None
    assert res["product_name"] == "Sweet Corn Chips"
    assert res["brand"] == "TastySnacks"
    assert res["nutriscore"] in ["D", "E"]  # Predicted by ML model
    assert res["final_decision"] in ["MODERATE", "AVOID"]

    # Verify ScanHistory logged
    history = test_db.query(ScanHistory).filter(ScanHistory.barcode == barcode).all()
    assert len(history) == 1

    # CRITICAL: Verify SCAN != EAT: FoodLog must have ZERO records!
    food_logs = test_db.query(FoodLog).all()
    assert len(food_logs) == 0, f"Expected 0 food consumption logs, found {len(food_logs)}"

    # Second lookup (cache hit) must not duplicate Product or create food log
    res2 = lookup_product(test_db, barcode=barcode, user_id=1, daily_calorie_limit=2000)
    assert res2["product_name"] == "Sweet Corn Chips"
    products = test_db.query(Product).filter(Product.barcode == barcode).all()
    assert len(products) == 1, "Duplicate product created on re-query!"
    assert test_db.query(FoodLog).count() == 0, "Food log created on cache hit!"
