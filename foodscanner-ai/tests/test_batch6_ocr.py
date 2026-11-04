from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-6-ocr"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db
from database.models import User, FoodLog
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.ocr_service import (
    OCRStatus,
    ServingBasis,
    ServingInfo,
    FSSAIStatus,
    OCRPreprocessor,
    classify_text_regions,
    detect_serving_basis,
    extract_ingredients_list,
    extract_fssai_license,
    extract_structured_nutrition,
    parse_structured_ocr,
    process_image_ocr,
    DISCLAIMER_TEXT,
    _OCR_PARSER_VERSION,
)
from api.main import app


@pytest.fixture(scope="module")
def client():
    orm_init_db()
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_token():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ocr_tester@example.com").first()
        if not user:
            user = User(
                email="ocr_tester@example.com",
                name="OCR Tester",
                hashed_password=hash_password("password123"),
                daily_calorie_limit=2000,
                created_at=db_service._utc_now_str(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user_id=int(user.id))
        return token
    finally:
        db.close()


def create_test_image_b64(width: int = 200, height: int = 100, color: str = "white") -> str:
    """Helper to generate in-memory PNG images as base64 strings."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ==============================================================================
# 1. NUTRITION FIELD EXTRACTION & PARSING
# ==============================================================================

def test_nutrition_field_extraction_comprehensive():
    """Verify complete extraction of all target nutritional fields from label text."""
    sample_label = """
    NUTRITIONAL INFORMATION
    Per 100g
    Energy: 440 kcal
    Protein: 8.5 g
    Carbohydrate: 64.0 g
    Total Sugars: 18.0 g
    Added Sugars: 10.5 g
    Total Fat: 15.0 g
    Saturated Fat: 4.2 g
    Trans Fat: 0.05 g
    Dietary Fibre: 5.5 g
    Sodium: 280 mg
    Salt: 0.70 g
    """
    res = parse_structured_ocr(sample_label)

    assert res["status"] == OCRStatus.SUCCESS
    assert res["ocr_version"] == _OCR_PARSER_VERSION
    assert res["calories"] == 440.0
    assert res["protein"] == 8.5
    assert res["carbs"] == 64.0
    assert res["sugar"] == 18.0
    assert res["fat"] == 15.0
    assert res["fiber"] == 5.5
    assert res["salt"] == 0.70

    nutr = res["nutrition"]
    assert nutr["energy_kcal"]["value"] == 440.0
    assert nutr["energy_kcal"]["unit"] == "kcal"
    assert nutr["added_sugars_g"]["value"] == 10.5
    assert nutr["saturated_fat_g"]["value"] == 4.2
    assert nutr["trans_fat_g"]["value"] == 0.05
    assert nutr["sodium_mg"]["value"] == 280.0
    assert nutr["salt_g"]["value"] == 0.70


def test_different_nutrition_label_formats_and_abbreviations():
    """Verify extraction across varying label abbreviations, column styles, and syntax."""
    alt_label = """
    Nutritional Value Per 100 g:
    Prot. 7.2g
    Total Fat: 12g
    Sat Fat 3.5 g
    Trans-Fat 0.0 g
    Carbs: 55 g
    Sugars 12.0g
    Fiber 4.0g
    Sodium 150 mg
    Cal: 360 kcal
    """
    res = parse_structured_ocr(alt_label)
    assert res["calories"] == 360.0
    assert res["protein"] == 7.2
    assert res["fat"] == 12.0
    assert res["carbs"] == 55.0
    assert res["sugar"] == 12.0
    assert res["fiber"] == 4.0
    assert res["nutrition"]["saturated_fat_g"]["value"] == 3.5
    assert res["nutrition"]["trans_fat_g"]["value"] == 0.0
    assert res["nutrition"]["sodium_mg"]["value"] == 150.0


# ==============================================================================
# 2. UNIT NORMALIZATION & CONVERSIONS
# ==============================================================================

def test_unit_normalization_kj_to_kcal():
    """Verify that energy declared in kJ is automatically converted to kcal."""
    label_kj = """
    NUTRITION FACTS
    Per 100g
    Energy 1883 kJ
    Protein 6.0 g
    Carbohydrate 70.0 g
    Fat 10.0 g
    """
    res = parse_structured_ocr(label_kj)
    # 1883 kJ / 4.184 = 450.0 kcal
    assert res["calories"] == 450.0
    assert res["nutrition"]["energy_kcal"]["unit"] == "kcal"
    assert res["nutrition"]["energy_kcal"]["raw_unit"] == "kj"


def test_unit_normalization_sodium_and_salt_cross_calculation():
    """Verify that sodium in grams is converted to mg, and salt is derived if missing."""
    # Label declares sodium in grams (0.16g) without explicit salt
    label_sod_g = """
    NUTRITIONAL FACTS (100g)
    Energy: 300 kcal
    Sodium: 0.16 g
    Protein: 5.0 g
    """
    res = parse_structured_ocr(label_sod_g)
    assert res["nutrition"]["sodium_mg"]["value"] == 160.0
    assert res["nutrition"]["sodium_mg"]["unit"] == "mg"
    # Salt derived: 160mg * 2.5 / 1000 = 0.40g
    assert res["salt"] == 0.40
    assert res["nutrition"]["salt_g"]["value"] == 0.40

    # Label declares salt without sodium: 0.50g salt -> 200mg sodium
    label_salt = """
    Per 100g
    Salt: 0.50 g
    Protein: 4.0 g
    """
    res_salt = parse_structured_ocr(label_salt)
    assert res_salt["salt"] == 0.50
    assert res_salt["nutrition"]["sodium_mg"]["value"] == 200.0


# ==============================================================================
# 3. SERVING BASIS (PER 100G, PER 100ML, PER SERVING)
# ==============================================================================

def test_serving_basis_detection_per_100g_and_per_100ml():
    """Verify correct detection of 100g vs 100ml basis."""
    text_g = "Nutritional Information Per 100 g: Energy 400 kcal"
    info_g = detect_serving_basis(text_g)
    assert info_g.basis == ServingBasis.PER_100G

    text_ml = "Nutritional Value Per 100 ml: Energy 45 kcal"
    info_ml = detect_serving_basis(text_ml)
    assert info_ml.basis == ServingBasis.PER_100ML


def test_serving_basis_per_serving_and_serving_size():
    """Verify serving size extraction and per-serving basis attribution."""
    text_serve = """
    Serving size: 25g
    Servings per pack: 4
    Per serving:
    Energy 110 kcal
    Protein 2.5 g
    Fat 4.0 g
    """
    info = detect_serving_basis(text_serve)
    assert info.basis == ServingBasis.PER_SERVING
    assert info.size_value == 25.0
    assert info.size_unit == "g"
    assert info.servings_per_pack == 4.0

    res = parse_structured_ocr(text_serve)
    assert res["serving"]["basis"] == ServingBasis.PER_SERVING
    assert res["serving"]["size_value"] == 25.0
    assert res["serving_size"] == 25.0
    assert any("per serving" in w.lower() for w in res["warnings"])


# ==============================================================================
# 4. INGREDIENT LIST EXTRACTION & SEQUENCE PRESERVATION
# ==============================================================================

def test_ingredient_extraction_and_order_preservation():
    """Verify ingredient list extraction, nested parenthesis preservation, and ordering."""
    label_text = """
    INGREDIENTS: Refined Wheat Flour (Maida), Edible Vegetable Oil (Palmolein, Rice Bran Oil), Sugar, Liquid Glucose, Cocoa Solids (3.5%), Iodised Salt, Invert Syrup.
    ALLERGEN INFORMATION: Contains Wheat and Soy.
    MFD BY: Good Foods Pvt Ltd.
    """
    ing = extract_ingredients_list(label_text)
    assert ing.confidence >= 0.85
    assert len(ing.items) == 7

    # Check exact order
    expected = [
        "Refined Wheat Flour (Maida)",
        "Edible Vegetable Oil (Palmolein, Rice Bran Oil)",
        "Sugar",
        "Liquid Glucose",
        "Cocoa Solids (3.5%)",
        "Iodised Salt",
        "Invert Syrup",
    ]
    assert ing.items == expected
    # Ensure nested comma did not split "Palmolein, Rice Bran Oil"
    assert "Edible Vegetable Oil (Palmolein, Rice Bran Oil)" in ing.items


def test_missing_ingredients_graceful():
    """Verify that labels with no ingredient section return an empty list without error."""
    text_no_ing = "NUTRITION FACTS: Energy 200 kcal, Protein 5g."
    ing = extract_ingredients_list(text_no_ing)
    assert ing.items == []
    assert ing.raw_text == ""
    assert ing.confidence == 0.0


# ==============================================================================
# 5. FSSAI LICENSE EXTRACTION & VALIDATION
# ==============================================================================

def test_fssai_license_extraction_detected():
    """Verify extraction of 14-digit FSSAI license numbers."""
    text1 = "Mfg by Best Foods Ltd. FSSAI Lic. No. 10012011000123. Net Wt 100g."
    f1 = extract_fssai_license(text1)
    assert f1.status == FSSAIStatus.DETECTED
    assert f1.value == "10012011000123"
    assert f1.confidence >= 0.90

    # Format without dots: "fssai lic no 11518018000456"
    text2 = "Batch 450 FSSAI Lic No: 11518018000456"
    f2 = extract_fssai_license(text2)
    assert f2.status == FSSAIStatus.DETECTED
    assert f2.value == "11518018000456"


def test_fssai_license_ambiguous_and_not_detected():
    """Verify that incomplete license numbers are flagged AMBIGUOUS, and missing is NOT_DETECTED."""
    # Malformed digit count (12 digits instead of 14)
    text_ambig = "FSSAI Lic. No. 100120110001"
    f_ambig = extract_fssai_license(text_ambig)
    assert f_ambig.status == FSSAIStatus.AMBIGUOUS
    assert f_ambig.value == "100120110001"

    # No license mentioned
    text_none = "Packaged Cornflakes. Best Before 12 months."
    f_none = extract_fssai_license(text_none)
    assert f_none.status == FSSAIStatus.NOT_DETECTED
    assert f_none.value is None


# ==============================================================================
# 6. VALIDATION & SANITY CHECKS
# ==============================================================================

def test_negative_values_rejected_and_flagged():
    """Verify that negative numeric values are rejected and never converted to zero."""
    label_neg = """
    Per 100g
    Energy: -450 kcal
    Protein: -8.0 g
    Carbohydrate: 50.0 g
    """
    serving_info = ServingInfo(basis=ServingBasis.PER_100G)
    nutr, warnings = extract_structured_nutrition(label_neg, serving_info)

    assert "energy_kcal" not in nutr
    assert "protein_g" not in nutr
    assert nutr["carbohydrates_g"].value == 50.0
    assert any("negative" in w.lower() for w in warnings)


def test_conflict_sugars_greater_than_carbohydrates_warned():
    """Verify that sugars exceeding total carbs is flagged as an OCR conflict warning."""
    label_conflict = """
    Per 100g
    Carbohydrate: 10.0 g
    Total Sugars: 25.0 g
    """
    serving_info = ServingInfo(basis=ServingBasis.PER_100G)
    nutr, warnings = extract_structured_nutrition(label_conflict, serving_info)

    assert nutr["sugars_g"].value == 25.0
    assert nutr["carbohydrates_g"].value == 10.0
    assert any("conflict" in w.lower() and "sugars" in w.lower() for w in warnings)


def test_dropped_decimal_point_correction():
    """Verify that common OCR dropped decimal point (e.g. 503 -> 50.3) is safely stabilized."""
    label_decimal = """
    Per 100g
    Carbohydrate: 503 g
    Protein: 125 g
    """
    serving_info = ServingInfo(basis=ServingBasis.PER_100G)
    nutr, warnings = extract_structured_nutrition(label_decimal, serving_info)

    assert nutr["carbohydrates_g"].value == 50.3
    assert nutr["protein_g"].value == 12.5
    assert any("dropped decimal point" in w.lower() for w in warnings)


# ==============================================================================
# 7. REGION CLASSIFICATION
# ==============================================================================

def test_multi_region_classification():
    """Verify packaging lines are separated into distinct semantic regions."""
    full_text = """
    Nutritional Information Per 100g:
    Energy: 400 kcal
    Protein: 8.0 g
    Carbohydrates: 60.0 g

    Ingredients: Whole Wheat Flour, Sugar, Vegetable Fat, Salt.

    Allergen Advice: Contains Gluten. May contain milk traces.

    FSSAI Lic. No. 10012011000123

    Mfd by: Snack Foods Ltd, Plot 45, Pune. Net Weight: 150g.
    """
    regions = classify_text_regions(full_text)
    types = [r.type for r in regions]

    assert "nutrition" in types
    assert "ingredients" in types
    assert "fssai_license" in types
    assert "allergen_info" in types
    assert "product_info" in types


# ==============================================================================
# 8. PREPROCESSING PIPELINE
# ==============================================================================

def test_ocr_preprocessor_pipeline():
    """Verify OCRPreprocessor handles small images, resizes, and handles corrupted bytes."""
    # 1. Valid small image -> upscaled
    img_small = Image.new("RGB", (400, 300), color="white")
    buf = io.BytesIO()
    img_small.save(buf, format="PNG")
    prep_img, meta = OCRPreprocessor.preprocess(buf.getvalue())
    assert prep_img is not None
    assert meta["upscaled"] is True
    assert prep_img.size[0] > 400

    # 2. Corrupt / empty bytes -> returns None
    corrupt_prep, corrupt_meta = OCRPreprocessor.preprocess(b"not an image")
    assert corrupt_prep is None
    assert corrupt_meta is None


# ==============================================================================
# 9. API ENDPOINT TESTS (POST /ocr)
# ==============================================================================

def test_ocr_api_endpoint_requires_auth(client):
    """Verify POST /ocr is protected by authentication."""
    res = client.post("/ocr", json={"image_base64": create_test_image_b64()})
    assert res.status_code == 401


def test_ocr_api_validation_errors(client, auth_token):
    """Verify POST /ocr rejects empty and invalid base64 payloads."""
    headers = {"Authorization": f"Bearer {auth_token}"}

    # Empty base64
    res_empty = client.post("/ocr", json={"image_base64": ""}, headers=headers)
    assert res_empty.status_code == 400

    # Invalid base64 characters
    res_inv = client.post("/ocr", json={"image_base64": "!!!not_base64!!!"}, headers=headers)
    assert res_inv.status_code == 400


def test_ocr_api_backward_compatibility_and_structure(client, auth_token):
    """Verify POST /ocr returns both legacy top-level keys and structured OCR 2.0 objects."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    img_b64 = create_test_image_b64(width=100, height=100)

    res = client.post("/ocr", json={"image_base64": img_b64}, headers=headers)
    assert res.status_code == 200

    data = res.json()
    # 1. Legacy fields preserved for mobile compatibility
    assert "product_name" in data
    assert "calories" in data
    assert "fat" in data
    assert "sugar" in data
    assert "salt" in data
    assert "protein" in data
    assert "fiber" in data
    assert "carbs" in data
    assert "serving_size" in data
    assert "confidence" in data
    assert "raw_text" in data

    # 2. Rich OCR 2.0 fields
    assert "status" in data
    assert data["ocr_version"] == _OCR_PARSER_VERSION
    assert "nutrition" in data
    assert "serving" in data
    assert "ingredients" in data
    assert "fssai_license" in data
    assert "regions" in data
    assert "disclaimer" in data
    assert DISCLAIMER_TEXT in data["disclaimer"]


# ==============================================================================
# 10. SCAN NOT EAT INVARIANT & BATCH 5 INTEGRATION
# ==============================================================================

def test_ocr_does_not_create_food_log(client, auth_token):
    """Verify that running OCR never logs food consumption (Scan != Eat invariant)."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ocr_tester@example.com").first()
        initial_count = db.query(FoodLog).filter(FoodLog.user_id == user.id).count()
    finally:
        db.close()

    img_b64 = create_test_image_b64()
    res = client.post("/ocr", json={"image_base64": img_b64}, headers=headers)
    assert res.status_code == 200

    db = SessionLocal()
    try:
        after_count = db.query(FoodLog).filter(FoodLog.user_id == user.id).count()
        assert after_count == initial_count, "OCR must not create food log entries!"
    finally:
        db.close()


def test_ocr_with_claim_verification_integration(client, auth_token):
    """Verify that optional claims passed to /ocr trigger Batch 5 claim verification."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    img_b64 = create_test_image_b64()

    payload = {
        "image_base64": img_b64,
        "claims": ["Sugar Free", "High Protein"],
    }
    res = client.post("/ocr", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert "claim_verification" in data
    claim_results = data["claim_verification"]
    assert "results" in claim_results
    assert "disclaimer" in claim_results
