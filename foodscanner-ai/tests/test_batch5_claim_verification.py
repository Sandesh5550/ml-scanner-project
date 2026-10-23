from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-5-claims"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from database.orm import SessionLocal, init_db as orm_init_db, engine
from database.models import User, Product, Nutrition, FoodLog
from services import db_service
from services.auth_service import hash_password, create_access_token
from services.claim_verification import (
    normalize_claim_text,
    verify_single_claim,
    verify_claims,
    REGULATORY_RULES,
    DISCLAIMER_TEXT,
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
        user = db.query(User).filter(User.email == "claims_tester@example.com").first()
        if not user:
            user = User(
                email="claims_tester@example.com",
                name="Claims Tester",
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


# ==============================================================================
# 1. CLAIM NORMALIZATION TESTS
# ==============================================================================

def test_claim_normalization_comprehensive():
    """Verify robust normalization across casing, hyphens, whitespace, and aliases."""
    test_cases = [
        # Sugar Free
        ("Sugar Free", "sugar_free"),
        ("SUGAR FREE", "sugar_free"),
        ("sugar-free", "sugar_free"),
        ("  Sugar  -  Free  ", "sugar_free"),
        ("zero sugar", "sugar_free"),
        ("0 Sugar", "sugar_free"),
        ("0% sugar", "sugar_free"),
        ("no sugar", "sugar_free"),
        # No Added Sugar
        ("No Added Sugar", "no_added_sugar"),
        ("NO ADDED SUGAR", "no_added_sugar"),
        ("No-Added-Sugar", "no_added_sugar"),
        ("no sugar added", "no_added_sugar"),
        ("zero added sugar", "no_added_sugar"),
        ("0 added sugar", "no_added_sugar"),
        ("without added sugar", "no_added_sugar"),
        # Low Fat
        ("Low Fat", "low_fat"),
        ("LOW-FAT", "low_fat"),
        ("low in fat", "low_fat"),
        ("little fat", "low_fat"),
        # Low Sodium
        ("Low Sodium", "low_sodium"),
        ("low-sodium", "low_sodium"),
        ("Low Salt", "low_sodium"),
        ("low-salt", "low_sodium"),
        ("low in salt", "low_sodium"),
        ("low in sodium", "low_sodium"),
        # High Fibre
        ("High Fibre", "high_fibre"),
        ("HIGH FIBER", "high_fibre"),
        ("high-fibre", "high_fibre"),
        ("high-fiber", "high_fibre"),
        ("rich in fibre", "high_fibre"),
        ("fiber rich", "high_fibre"),
        ("excellent source of fibre", "high_fibre"),
        # High Protein
        ("High Protein", "high_protein"),
        ("HIGH-PROTEIN", "high_protein"),
        ("rich in protein", "high_protein"),
        ("protein rich", "high_protein"),
        ("excellent source of protein", "high_protein"),
        # Zero Trans Fat
        ("Zero Trans Fat", "zero_trans_fat"),
        ("trans fat free", "zero_trans_fat"),
        ("trans-fat free", "zero_trans_fat"),
        ("0g trans fat", "zero_trans_fat"),
        ("no trans fat", "zero_trans_fat"),
        # Unknown claims
        ("Immunity Booster", "immunity_booster"),
        ("Keto Friendly", "keto_friendly"),
        ("", ""),
    ]

    for raw, expected in test_cases:
        actual = normalize_claim_text(raw)
        assert actual == expected, f"Expected '{raw}' -> '{expected}', got '{actual}'"


# ==============================================================================
# 2. KNOWN SUPPORTED CLAIMS
# ==============================================================================

def test_all_seven_claims_supported():
    """Verify that each of the 7 target claims succeeds when meeting FSSAI criteria."""
    # 1. Sugar Free (<= 0.5g/100g)
    res_sug_free = verify_single_claim("Sugar Free", nutrition={"sugar": 0.2})
    assert res_sug_free["status"] == "SUPPORTED"
    assert res_sug_free["rule_id"] == "FSSAI-ACR-2018-SCH1-SUG-FREE"
    assert res_sug_free["evidence"]["comparison"] == "<="

    # 2. Low Fat (<= 3.0g/100g)
    res_low_fat = verify_single_claim("Low Fat", nutrition={"fat": 2.4})
    assert res_low_fat["status"] == "SUPPORTED"
    assert res_low_fat["rule_id"] == "FSSAI-ACR-2018-SCH1-FAT-LOW"

    # 3. Low Sodium (<= 0.12g sodium or <= 0.3g salt per 100g)
    res_low_sod_val = verify_single_claim("Low Sodium", nutrition={"sodium": 0.08})
    assert res_low_sod_val["status"] == "SUPPORTED"
    assert res_low_sod_val["rule_id"] == "FSSAI-ACR-2018-SCH1-SOD-LOW"

    res_low_salt = verify_single_claim("Low Salt", nutrition={"salt": 0.25})
    assert res_low_salt["status"] == "SUPPORTED"

    res_low_sod_mg = verify_single_claim("Low Sodium", nutrition={"sodium": "90mg"})
    assert res_low_sod_mg["status"] == "SUPPORTED"

    # 4. High Fibre (>= 6.0g/100g)
    res_high_fbr = verify_single_claim("High Fibre", nutrition={"fiber": 8.0})
    assert res_high_fbr["status"] == "SUPPORTED"
    assert res_high_fbr["rule_id"] == "FSSAI-ACR-2018-SCH1-FBR-HIGH"

    # 5. High Protein (>= 20% of 54g adult RDA = >= 10.8g/100g)
    res_high_prot = verify_single_claim("High Protein", nutrition={"protein": 14.5})
    assert res_high_prot["status"] == "SUPPORTED"
    assert res_high_prot["rule_id"] == "FSSAI-ACR-2018-SCH1-PROT-HIGH"

    # 6. Zero Trans Fat (< 0.2g trans fat and sat fat <= 1.5g)
    res_trans_fat = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"trans_fat": 0.05, "saturated_fat": 1.1},
    )
    assert res_trans_fat["status"] == "SUPPORTED"
    assert res_trans_fat["rule_id"] == "FSSAI-ACR-2018-SCH1-TRANSFAT-FREE"

    # 7. No Added Sugar (no added sugars/syrups in ingredient list)
    res_no_added_sug = verify_single_claim(
        "No Added Sugar",
        nutrition={"sugar": 2.5},
        ingredients="Rolled oats, dried almonds, chia seeds, cardamom",
    )
    assert res_no_added_sug["status"] == "SUPPORTED"
    assert res_no_added_sug["rule_id"] == "FSSAI-ACR-2018-REG4-SUG-NO-ADDED"


# ==============================================================================
# 3. KNOWN UNSUPPORTED CLAIMS
# ==============================================================================

def test_all_seven_claims_unsupported():
    """Verify that claims exceeding thresholds return NOT_SUPPORTED."""
    # 1. Sugar Free exceeded (> 0.5g)
    res1 = verify_single_claim("Sugar Free", nutrition={"sugar": 4.5})
    assert res1["status"] == "NOT_SUPPORTED"
    assert res1["evidence"]["comparison"] == ">"

    # 2. Low Fat exceeded (> 3.0g)
    res2 = verify_single_claim("Low Fat", nutrition={"fat": 5.2})
    assert res2["status"] == "NOT_SUPPORTED"

    # 3. Low Sodium exceeded (> 0.12g sodium)
    res3 = verify_single_claim("Low Sodium", nutrition={"sodium": 0.35})
    assert res3["status"] == "NOT_SUPPORTED"

    # 4. High Fibre not met (< 6.0g)
    res4 = verify_single_claim("High Fibre", nutrition={"fiber": 3.2})
    assert res4["status"] == "NOT_SUPPORTED"

    # 5. High Protein not met (< 10.8g)
    res5 = verify_single_claim("High Protein", nutrition={"protein": 6.0})
    assert res5["status"] == "NOT_SUPPORTED"

    # 6. Trans Fat exceeded (>= 0.2g)
    res6 = verify_single_claim("Zero Trans Fat", nutrition={"trans_fat": 0.4})
    assert res6["status"] == "NOT_SUPPORTED"

    # 6b. Trans fat low (< 0.2g) but saturated fat exceeds 1.5g limit
    res6b = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"trans_fat": 0.1, "saturated_fat": 3.5},
    )
    assert res6b["status"] == "NOT_SUPPORTED"
    assert "saturated fat" in res6b["reason"].lower()

    # 7. No Added Sugar with added sugar ingredient detected
    res7 = verify_single_claim(
        "No Added Sugar",
        ingredients="Whole wheat flour, cane sugar, palm oil, cocoa butter",
    )
    assert res7["status"] == "NOT_SUPPORTED"
    assert "cane sugar" in res7["evidence"]["flagged_ingredients"]

    # 7b. Multiple added sugar patterns
    res7b = verify_single_claim(
        "No Added Sugar",
        ingredients="Corn flakes, high fructose corn syrup, invert syrup, honey",
    )
    assert res7b["status"] == "NOT_SUPPORTED"
    assert len(res7b["evidence"]["flagged_ingredients"]) >= 2


# ==============================================================================
# 4. BOUNDARY-VALUE TESTS
# ==============================================================================

def test_boundary_value_thresholds():
    """Verify exact boundary values on FSSAI limits."""
    # Sugar Free: threshold 0.50g
    assert verify_single_claim("Sugar Free", nutrition={"sugar": 0.50})["status"] == "SUPPORTED"
    assert verify_single_claim("Sugar Free", nutrition={"sugar": 0.51})["status"] == "NOT_SUPPORTED"
    assert verify_single_claim("Sugar Free", nutrition={"sugar": 0.0})["status"] == "SUPPORTED"

    # Low Fat: threshold 3.00g
    assert verify_single_claim("Low Fat", nutrition={"fat": 3.00})["status"] == "SUPPORTED"
    assert verify_single_claim("Low Fat", nutrition={"fat": 3.01})["status"] == "NOT_SUPPORTED"

    # Low Sodium: threshold 0.12g sodium / 0.30g salt
    assert verify_single_claim("Low Sodium", nutrition={"sodium": 0.12})["status"] == "SUPPORTED"
    assert verify_single_claim("Low Sodium", nutrition={"sodium": 0.121})["status"] == "NOT_SUPPORTED"
    assert verify_single_claim("Low Sodium", nutrition={"salt": 0.30})["status"] == "SUPPORTED"
    assert verify_single_claim("Low Sodium", nutrition={"salt": 0.31})["status"] == "NOT_SUPPORTED"

    # High Fibre: threshold 6.00g
    assert verify_single_claim("High Fibre", nutrition={"fiber": 6.00})["status"] == "SUPPORTED"
    assert verify_single_claim("High Fibre", nutrition={"fiber": 5.99})["status"] == "NOT_SUPPORTED"

    # High Protein: threshold 10.80g (20% of 54g)
    assert verify_single_claim("High Protein", nutrition={"protein": 10.80})["status"] == "SUPPORTED"
    assert verify_single_claim("High Protein", nutrition={"protein": 10.79})["status"] == "NOT_SUPPORTED"

    # Zero Trans Fat: strictly < 0.20g
    assert verify_single_claim("Zero Trans Fat", nutrition={"trans_fat": 0.19})["status"] == "SUPPORTED"
    assert verify_single_claim("Zero Trans Fat", nutrition={"trans_fat": 0.20})["status"] == "NOT_SUPPORTED"


# ==============================================================================
# 5. MISSING DATA TESTS (INSUFFICIENT_DATA)
# ==============================================================================

def test_missing_nutrition_returns_insufficient_data():
    """Verify that missing required nutrients yield INSUFFICIENT_DATA and never guess."""
    empty_nutrition = {}

    for claim in ["Sugar Free", "Low Fat", "Low Sodium", "High Fibre", "High Protein", "Zero Trans Fat"]:
        res = verify_single_claim(claim, nutrition=empty_nutrition)
        assert res["status"] == "INSUFFICIENT_DATA", f"Expected INSUFFICIENT_DATA for '{claim}' with missing data"
        assert res["data_quality"] == "LOW"

    # Null values should also yield INSUFFICIENT_DATA
    null_nutrition = {"sugar": None, "fat": None, "sodium": None, "fiber": None, "protein": None, "trans_fat": None}
    for claim in ["Sugar Free", "Low Fat", "Low Sodium", "High Fibre", "High Protein", "Zero Trans Fat"]:
        res = verify_single_claim(claim, nutrition=null_nutrition)
        assert res["status"] == "INSUFFICIENT_DATA", f"Expected INSUFFICIENT_DATA for null values on '{claim}'"


def test_missing_ingredients_for_no_added_sugar():
    """Verify that No Added Sugar returns INSUFFICIENT_DATA when ingredients declaration is missing."""
    # No ingredients at all
    res1 = verify_single_claim("No Added Sugar", nutrition={"sugar": 1.0})
    assert res1["status"] == "INSUFFICIENT_DATA"
    assert "ingredient list is missing" in res1["reason"].lower()

    # Empty string ingredients
    res2 = verify_single_claim("No Added Sugar", nutrition={"sugar": 1.0}, ingredients="")
    assert res2["status"] == "INSUFFICIENT_DATA"

    # Empty list ingredients
    res3 = verify_single_claim("No Added Sugar", nutrition={"sugar": 1.0}, ingredients=[])
    assert res3["status"] == "INSUFFICIENT_DATA"


# ==============================================================================
# 6. UNKNOWN AND UNSUPPORTED CLAIMS (NEEDS_REVIEW)
# ==============================================================================

def test_unknown_claim_returns_needs_review():
    """Verify that claims outside active FSSAI definitions return NEEDS_REVIEW."""
    unknown_claims = [
        "Immunity Booster",
        "Keto Certified",
        "Superfood Antioxidant",
        "Cleanses Toxins",
        "Ayurvedic Vitality",
    ]

    for uc in unknown_claims:
        res = verify_single_claim(uc, nutrition={"protein": 20.0, "sugar": 0.0})
        assert res["status"] == "NEEDS_REVIEW", f"Expected NEEDS_REVIEW for unknown claim '{uc}'"
        assert res["rule_id"] is None
        assert res["source"] is None
        assert res["data_quality"] == "UNKNOWN"


# ==============================================================================
# 7. MULTIPLE AND DUPLICATE CLAIMS
# ==============================================================================

def test_multiple_and_duplicate_claims_batch():
    """Verify batch verification deduplicates while preserving distinct claims and order."""
    claims = [
        "Sugar Free",
        "SUGAR-FREE",
        "0 sugar",
        "High Protein",
        "high protein",
        "Low Fat",
    ]
    nutrition = {
        "sugar": 0.2,
        "protein": 15.0,
        "fat": 1.5,
    }

    res = verify_claims(claims, nutrition=nutrition)
    assert res["total_claims"] == 3
    assert len(res["results"]) == 3

    normalized_keys = [r["normalized_claim"] for r in res["results"]]
    assert normalized_keys == ["sugar_free", "high_protein", "low_fat"]

    for r in res["results"]:
        assert r["status"] == "SUPPORTED"

    assert res["disclaimer"] == DISCLAIMER_TEXT


# ==============================================================================
# 8. RULE METADATA AND REGULATORY TRACEABILITY
# ==============================================================================

def test_rule_metadata_and_regulatory_traceability():
    """Verify that every supported rule has complete FSSAI regulatory metadata."""
    res = verify_single_claim("High Protein", nutrition={"protein": 12.0})
    source = res["source"]
    assert source is not None
    assert source["authority"] == "FSSAI"
    assert "Advertising and Claims" in source["document"]
    assert "Schedule I" in source["reference"]
    assert source["version"] == "2018.1"
    assert source["effective_date"] == "2019-07-01"
    assert res["rule_id"] == "FSSAI-ACR-2018-SCH1-PROT-HIGH"


# ==============================================================================
# 9. DETERMINISTIC REPEATED RESULTS
# ==============================================================================

def test_deterministic_repeated_results():
    """Verify that verification produces 100% deterministic identical outputs across repetitions."""
    nutrition = {"sugar": 0.4, "fat": 2.0, "protein": 12.0}
    claims = ["Sugar Free", "Low Fat", "High Protein"]

    baseline = verify_claims(claims, nutrition=nutrition)
    for _ in range(25):
        run = verify_claims(claims, nutrition=nutrition)
        assert run == baseline


# ==============================================================================
# 10. API ENDPOINT TESTS (POST /verify-claims)
# ==============================================================================

def test_verify_claims_endpoint_requires_auth(client):
    """Verify that POST /verify-claims is protected and rejects unauthenticated requests."""
    res = client.post("/verify-claims", json={"claims": ["Sugar Free"]})
    assert res.status_code == 401


def test_verify_claims_endpoint_authenticated(client, auth_token):
    """Verify POST /verify-claims with valid JWT token succeeds with complete response structure."""
    payload = {
        "claims": ["Sugar Free", "High Protein", "No Added Sugar"],
        "nutrition": {
            "sugar": 0.3,
            "protein": 14.0,
        },
        "ingredients": "Whole oats, almonds, sunflower seeds",
    }
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = client.post("/verify-claims", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert "results" in data
    assert "total_claims" in data
    assert "disclaimer" in data
    assert data["total_claims"] == 3

    results = {r["normalized_claim"]: r for r in data["results"]}
    assert results["sugar_free"]["status"] == "SUPPORTED"
    assert results["high_protein"]["status"] == "SUPPORTED"
    assert results["no_added_sugar"]["status"] == "SUPPORTED"


def test_verify_claims_endpoint_with_barcode_enrichment(client, auth_token):
    """Verify POST /verify-claims loads product nutrition and ingredients from DB if barcode is passed."""
    barcode = "8905555555555"
    db = SessionLocal()
    try:
        existing = db.query(Product).filter(Product.barcode == barcode).first()
        if not existing:
            p = Product(
                barcode=barcode,
                product_name="Database Enriched Cereal",
                brand="TestBrand",
                ingredients="Oats, wheat flakes, cocoa powder, natural aroma",
                created_at=db_service._utc_now_str(),
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            nutr = Nutrition(
                product_id=p.id,
                calories=350.0,
                fat=2.5,
                sugar=0.4,
                salt=0.2,
                protein=12.5,
                fiber=7.0,
                carbs=60.0,
            )
            db.add(nutr)
            db.commit()
    finally:
        db.close()

    # Pass barcode without explicit nutrition or ingredients
    payload = {
        "barcode": barcode,
        "claims": ["Sugar Free", "Low Fat", "High Protein", "No Added Sugar"],
    }
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = client.post("/verify-claims", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["total_claims"] == 4
    for item in data["results"]:
        assert item["status"] == "SUPPORTED", f"Claim '{item['claim']}' was not SUPPORTED: {item['reason']}"


def test_verify_claims_empty_and_invalid_inputs(client, auth_token):
    """Verify graceful handling of empty lists, blank strings, and invalid payloads."""
    headers = {"Authorization": f"Bearer {auth_token}"}

    # Empty claims array
    res1 = client.post("/verify-claims", json={"claims": []}, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["total_claims"] == 0
    assert res1.json()["results"] == []

    # Claims array with only whitespace
    res2 = client.post("/verify-claims", json={"claims": ["  ", ""]}, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["total_claims"] == 0

    # Non-list claims
    res3 = client.post("/verify-claims", json={"claims": "Sugar Free"}, headers=headers)
    assert res3.status_code in (400, 422)


# ==============================================================================
# 11. SCAN INTEGRATION & INVARIANTS (HEALTH SCORE & SCAN NOT EAT)
# ==============================================================================

def test_scan_integration_additive_and_invariants(client, auth_token):
    """Verify that adding claims to /scan is additive, leaves health_score unchanged, and creates no food log."""
    barcode = "8909876543210"
    db = SessionLocal()
    try:
        existing = db.query(Product).filter(Product.barcode == barcode).first()
        if not existing:
            p = Product(
                barcode=barcode,
                product_name="Scan Invariant Test Biscuit",
                ingredients="Wheat flour, sugar, vegetable oil, salt",
                created_at=db_service._utc_now_str(),
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            nutr = Nutrition(
                product_id=p.id,
                calories=450.0,
                fat=15.0,
                sugar=20.0,
                salt=0.8,
                protein=6.0,
                fiber=2.0,
                carbs=70.0,
            )
            db.add(nutr)
            db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {auth_token}"}

    # Baseline: /scan without claims
    res_base = client.post("/scan", json={"barcode": barcode}, headers=headers)
    assert res_base.status_code == 200
    data_base = res_base.json()
    assert "claim_verification" not in data_base
    base_health_score = data_base["analysis"]["health_score"]

    # Extended: /scan with claims
    res_ext = client.post(
        "/scan",
        json={"barcode": barcode, "claims": ["Sugar Free", "High Protein"]},
        headers=headers,
    )
    assert res_ext.status_code == 200
    data_ext = res_ext.json()
    assert "claim_verification" in data_ext
    ext_health_score = data_ext["analysis"]["health_score"]

    # Invariant 1: Health score remains IDENTICAL (claim verification does NOT touch health score)
    assert ext_health_score == base_health_score

    # Invariant 2: Backward compatibility - all standard fields present
    assert data_ext["product"]["name"] == data_base["product"]["name"]
    assert data_ext["decision"]["final_decision"] == data_base["decision"]["final_decision"]

    # Invariant 3: Scan != Eat - no food log created by /scan or /verify-claims
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "claims_tester@example.com").first()
        food_logs = db.query(FoodLog).filter(FoodLog.user_id == user.id, FoodLog.barcode == barcode).all()
        assert len(food_logs) == 0, "Scan must not log food consumption!"
    finally:
        db.close()
