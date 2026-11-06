from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from services.auth_service import create_access_token
from api.main import app
from database.orm import SessionLocal
from database.models import Product, Nutrition, User
from ml_model.predict_nutriscore import (
    MODEL_PATH,
    EXPECTED_FEATURES,
    ModelArtifactError,
    extract_feature_vector,
    calculate_heuristic_nutriscore,
    predict_nutriscore,
    predict_nutriscore_with_fallback,
    predict_nutriscore_details,
)
from services.barcode_lookup import lookup_product
from services.recommendation_engine import get_healthier_alternatives


@pytest.fixture
def auth_user():
    """Create a temporary authenticated user with valid bearer token for tests."""
    email = f"ml_consistency_{uuid.uuid4().hex[:6]}@example.com"
    with SessionLocal() as db:
        user = User(
            email=email,
            hashed_password="hashed_password_123",
            name="ML Consistency Tester",
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


# ==============================================================================
# 1. PREPROCESSING AND FEATURE ORDER CONSISTENCY TESTS
# ==============================================================================

def test_feature_order_and_bounds_preprocessing():
    """Verify feature vector extraction preserves 7-feature order, bounds clipping, and imputation."""
    assert EXPECTED_FEATURES == ["calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"]
    assert "saturated_fat" not in EXPECTED_FEATURES
    assert "trans_fat" not in EXPECTED_FEATURES

    # Normal nutrition dict
    raw_sample = {
        "calories": 250.0,
        "fat": 12.0,
        "saturated_fat": 4.0,  # Should be ignored by 7-feature extraction
        "trans_fat": 0.1,      # Should be ignored by 7-feature extraction
        "sugar": 15.0,
        "salt": 0.8,
        "protein": 5.0,
        "fiber": 2.0,
        "carbs": 30.0,
    }
    vec, values_dict = extract_feature_vector(raw_sample)
    assert len(vec) == 7
    assert vec == [250.0, 12.0, 15.0, 0.8, 5.0, 2.0, 30.0]
    assert "saturated_fat" not in values_dict
    assert "trans_fat" not in values_dict

    # Imputation: missing/None/empty string should default to 0.0
    partial_sample = {
        "calories": None,
        "sugar": "  ",
        "protein": "6.5",
    }
    vec_partial, _ = extract_feature_vector(partial_sample)
    assert vec_partial == [0.0, 0.0, 0.0, 0.0, 6.5, 0.0, 0.0]

    # Bounds clipping aligned with training distribution:
    # calories clipped to [0, 950], others clipped to [0, 100]
    extreme_sample = {
        "calories": 1500.0,
        "fat": 150.0,
        "sugar": -10.0,
        "salt": 200.0,
        "protein": -5.0,
        "fiber": 50.0,
        "carbs": 120.0,
    }
    vec_clipped, _ = extract_feature_vector(extreme_sample, clip_to_bounds=True)
    assert vec_clipped[0] == 950.0  # calories capped
    assert vec_clipped[1] == 100.0  # fat capped
    assert vec_clipped[2] == 0.0    # negative sugar clamped to 0
    assert vec_clipped[3] == 100.0  # salt capped
    assert vec_clipped[4] == 0.0    # negative protein clamped to 0
    assert vec_clipped[5] == 50.0
    assert vec_clipped[6] == 100.0  # carbs capped


def test_no_fallback_for_unrelated_programming_errors():
    """Verify that unrelated programming errors (non-dict inputs, etc.) raise TypeError, not heuristic."""
    for invalid_input in [None, 12345, "invalid_string", ["list", "of", "items"]]:
        with pytest.raises(TypeError, match="Expected nutrition_dict to be a dict"):
            predict_nutriscore_with_fallback(invalid_input)  # type: ignore

        with pytest.raises(TypeError, match="Expected nutrition_dict to be a dict"):
            predict_nutriscore(invalid_input)  # type: ignore

        with pytest.raises(TypeError, match="Expected nutrition_dict to be a dict"):
            extract_feature_vector(invalid_input)  # type: ignore

        with pytest.raises(TypeError, match="Expected nutrition_dict to be a dict"):
            predict_nutriscore_details(invalid_input)  # type: ignore


# ==============================================================================
# 2. CONTROLLED HEURISTIC FALLBACK TESTS
# ==============================================================================

def test_heuristic_fallback_when_model_artifact_missing_or_invalid(tmp_path):
    """Verify controlled fallback to heuristic ONLY when model artifact is missing or corrupted."""
    sample = {"calories": 120.0, "fat": 2.0, "sugar": 3.0, "salt": 0.1, "protein": 3.0, "fiber": 2.0, "carbs": 20.0}
    expected_heuristic = calculate_heuristic_nutriscore(sample)

    missing_path = tmp_path / "nonexistent_model.pkl"
    corrupt_path = tmp_path / "corrupt_model.pkl"
    corrupt_path.write_text("corrupted content not pickle", encoding="utf-8")

    # 1. Strict predict_nutriscore() still strictly raises errors
    with pytest.raises(FileNotFoundError):
        predict_nutriscore(sample, model_path=missing_path)

    with pytest.raises(ModelArtifactError):
        predict_nutriscore(sample, model_path=corrupt_path)

    # 2. predict_nutriscore_with_fallback gracefully falls back to heuristic
    fb_missing = predict_nutriscore_with_fallback(sample, model_path=missing_path)
    assert fb_missing == expected_heuristic
    assert fb_missing in ["A", "B", "C", "D", "E"]

    fb_corrupt = predict_nutriscore_with_fallback(sample, model_path=corrupt_path)
    assert fb_corrupt == expected_heuristic
    assert fb_corrupt in ["A", "B", "C", "D", "E"]


# ==============================================================================
# 3. IDENTICAL PREDICTIONS ACROSS PREDICTION FUNCTIONS
# ==============================================================================

def test_identical_predictions_across_prediction_functions():
    """Verify predict_nutriscore, predict_nutriscore_with_fallback, and details return identical grades."""
    samples = [
        {"calories": 45.0, "fat": 0.2, "sugar": 2.1, "salt": 0.02, "protein": 1.2, "fiber": 2.8, "carbs": 9.5},
        {"calories": 540.0, "fat": 36.0, "sugar": 52.0, "salt": 2.1, "protein": 4.5, "fiber": 0.5, "carbs": 58.0},
        {"calories": 210.0, "fat": 7.0, "sugar": 14.0, "salt": 0.9, "protein": 4.0, "fiber": 1.5, "carbs": 32.0},
    ]

    for s in samples:
        grade_strict = predict_nutriscore(s)
        grade_fallback = predict_nutriscore_with_fallback(s)
        details = predict_nutriscore_details(s)

        assert grade_strict == grade_fallback == details["nutriscore"]
        assert grade_strict in ["A", "B", "C", "D", "E"]
        assert len(details["probabilities"]) == 5
        assert abs(sum(details["probabilities"].values()) - 1.0) < 0.01


# ==============================================================================
# 4. CONSISTENT PREDICTIONS ACROSS /scan, /compare, AND RECOMMENDATIONS
# ==============================================================================

def test_consistent_predictions_across_routes_and_services(auth_user):
    """Verify /scan, /compare, and recommendation logic produce consistent ML predictions for identical inputs."""
    client = TestClient(app)

    # Test item with known nutritional profile
    test_nutrition = {
        "calories": 480.0,
        "fat": 24.0,
        "saturated_fat": 11.0,
        "trans_fat": 0.0,
        "sugar": 38.0,
        "salt": 1.2,
        "protein": 4.0,
        "fiber": 1.0,
        "carbs": 62.0,
    }
    expected_ml_grade = predict_nutriscore(test_nutrition)

    # 1. Direct prediction on barcode lookup / scan flow
    mock_payload = {
        "status": 1,
        "product": {
            "product_name": "ML Consistency Test Snack",
            "brands": "PramaanBrand",
            "nutriscore_grade": None,  # Force model prediction
            "nutriments": {
                "energy-kcal_100g": test_nutrition["calories"],
                "fat_100g": test_nutrition["fat"],
                "saturated-fat_100g": test_nutrition["saturated_fat"],
                "trans-fat_100g": test_nutrition["trans_fat"],
                "sugars_100g": test_nutrition["sugar"],
                "salt_100g": test_nutrition["salt"],
                "proteins_100g": test_nutrition["protein"],
                "fiber_100g": test_nutrition["fiber"],
                "carbohydrates_100g": test_nutrition["carbs"],
            },
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    mock_resp.raise_for_status.return_value = None

    barcode = f"8909{uuid.uuid4().int % 10**8:08d}"
    barcode_cmp = f"8908{uuid.uuid4().int % 10**8:08d}"

    try:
        with patch("httpx.Client.get", return_value=mock_resp):
            res_scan = client.post("/scan", json={"barcode": barcode}, headers=auth_user["headers"])
        assert res_scan.status_code == 200
        data_scan = res_scan.json()
        assert data_scan["product"]["nutriscore"] == expected_ml_grade

        # 2. Seed a separate test product for /compare without pre-existing nutriscore
        with SessionLocal() as db:
            prod_cmp = Product(
                barcode=barcode_cmp,
                product_name="Compare Test Snack Unscored",
                brand="CompareBrand",
                nutriscore=None,  # No pre-stored nutriscore
                created_at="2026-11-05 12:00:00",
            )
            db.add(prod_cmp)
            db.flush()
            nutr_cmp = Nutrition(
                product_id=prod_cmp.id,
                calories=test_nutrition["calories"],
                fat=test_nutrition["fat"],
                saturated_fat=test_nutrition["saturated_fat"],
                trans_fat=test_nutrition["trans_fat"],
                sugar=test_nutrition["sugar"],
                salt=test_nutrition["salt"],
                protein=test_nutrition["protein"],
                fiber=test_nutrition["fiber"],
                carbs=test_nutrition["carbs"],
            )
            db.add(nutr_cmp)
            db.commit()

        # Call /compare comparing the scanned item and the seeded item
        res_compare = client.post(
            "/compare",
            json={"product_a": barcode, "product_b": barcode_cmp},
            headers=auth_user["headers"],
        )
        assert res_compare.status_code == 200
        data_cmp = res_compare.json()

        # Both product_a and product_b had identical nutrition -> must have identical NutriScore grade matching ML
        assert data_cmp["product_a"]["nutriscore"] == expected_ml_grade
        assert data_cmp["product_b"]["nutriscore"] == expected_ml_grade

        # 3. Recommendations / alternatives candidates
        alts = get_healthier_alternatives("ML Consistency Test Snack", test_nutrition, limit=3)
        assert len(alts) > 0
        for alt in alts:
            assert "nutriscore" in alt
            assert alt["nutriscore"] in ["A", "B", "C", "D", "E"]
    finally:
        with SessionLocal() as db:
            for b in [barcode, barcode_cmp, "8909988776655", "8909988776699"]:
                p = db.query(Product).filter(Product.barcode == b).first()
                if p:
                    db.query(Nutrition).filter(Nutrition.product_id == p.id).delete()
                    db.delete(p)
            db.commit()


# ==============================================================================
# 5. TRAINING CLI SAFETY AND CONTRACT VALIDATION
# ==============================================================================

def test_training_cli_advanced_safety():
    """Verify running train_model.py with --advanced exits with error code 2 and does not overwrite model."""
    mtime_before = MODEL_PATH.stat().st_mtime
    size_before = MODEL_PATH.stat().st_size

    script_path = Path(__file__).resolve().parents[1] / "ml_model" / "train_model.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--advanced"],
        capture_output=True,
        text=True,
    )

    # Must exit with code 2 (argparse error)
    assert proc.returncode == 2
    assert "The --advanced flag is disabled in production" in proc.stderr

    # Artifact must remain completely untouched
    assert MODEL_PATH.stat().st_mtime == mtime_before
    assert MODEL_PATH.stat().st_size == size_before


def test_analyze_preserves_contract(auth_user):
    """Verify /analyze continues to return nutriscore: None conforming to its existing contract."""
    client = TestClient(app)
    res = client.post(
        "/analyze",
        json={
            "product_name": "Contract Check Oats",
            "calories": 380.0,
            "fat": 7.0,
            "sugar": 1.0,
            "salt": 0.01,
            "protein": 13.0,
            "fiber": 10.0,
            "carbs": 68.0,
        },
        headers=auth_user["headers"],
    )
    assert res.status_code == 200
    data = res.json()
    assert "product" in data
    assert data["product"]["nutriscore"] is None


def test_experimental_artifacts_not_imported_in_production():
    """Verify experimental models (SVM, XGBoost, standalone GB) are not referenced by production code."""
    prod_files = [
        Path(__file__).resolve().parents[1] / "api" / "main.py",
        Path(__file__).resolve().parents[1] / "services" / "barcode_lookup.py",
        Path(__file__).resolve().parents[1] / "services" / "recommendation_engine.py",
        Path(__file__).resolve().parents[1] / "ml_model" / "predict_nutriscore.py",
    ]
    experimental_model_names = [
        "gradient_boosting_model.pkl",
        "svm_model.pkl",
        "xgboost_model.pkl",
    ]

    for p in prod_files:
        content = p.read_text(encoding="utf-8")
        for exp_name in experimental_model_names:
            assert exp_name not in content, f"Production file {p.name} must not reference experimental artifact {exp_name}"

