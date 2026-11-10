from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-refactored-endpoints-phase6"
os.environ["FOODSCANNER_OCR_ENGINE"] = "tesseract"

from api.main import app
from database.init_db import init_db
from database.models import Nutrition, Product, ScanHistory, User
from database.orm import SessionLocal, init_db as orm_init_db
from schemas.product import (
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
)
from services import db_service
from services.auth_service import create_access_token, hash_password
from services.off_client import reset_default_off_client
from services.product_data_service import get_default_product_data_service
from services.scoring_engine import get_default_scoring_engine


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema is initialized."""
    init_db()
    orm_init_db()


@pytest.fixture(autouse=True)
def reset_off_rate_limiter():
    """Reset default OpenFoodFactsClient singleton to prevent 429 during large test runs."""
    reset_default_off_client()
    yield
    reset_default_off_client()


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_user(db_session):
    """Create or retrieve a standard test user with a valid JWT token."""
    email = "phase6_standard_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Phase6 Standard User",
            hashed_password=hash_password("SecurePassword123!"),
            daily_calorie_limit=2000,
            age=28,
            weight=70.0,
            height=175.0,
            diet_type=None,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _make_canonical(
    barcode: str,
    name: str,
    calories: float | None = 250.0,
    fat: float | None = 10.0,
    saturated_fat: float | None = 2.0,
    trans_fat: float | None = 0.0,
    carbs: float | None = 35.0,
    sugar: float | None = 4.0,
    protein: float | None = 8.0,
    fiber: float | None = 3.0,
    salt: float | None = 0.5,
    ingredients: str | None = "Wheat flour, water, salt, yeast",
    nutriscore: str | None = None,
) -> CanonicalProduct:
    now_dt = datetime.now(timezone.utc)
    nutrition = Nutrition100g(
        calories=calories,
        fat=fat,
        saturated_fat=saturated_fat,
        trans_fat=trans_fat,
        carbs=carbs,
        sugar=sugar,
        protein=protein,
        fiber=fiber,
        salt=salt,
    )
    return CanonicalProduct(
        canonical_id=barcode,
        barcode=barcode,
        product_name=name,
        brand="Phase6 Brand",
        category="Staples",
        nutrition=nutrition,
        ingredients=ingredients,
        additives=None,
        nutriscore=nutriscore,
        is_nutriscore_predicted=False,
        primary_provenance=DataProvenance.OFF_API_V3,
        field_provenance={},
        last_fetched_at=now_dt,
        last_updated_at=now_dt,
    )


# ==============================================================================
# 1. GET /product/{barcode}
# ==============================================================================

class TestGetProductEndpoint:
    def test_local_db_hit(self, client, auth_user, db_session):
        """Product present in DB returns 200 with complete nutrition and no scan history."""
        bcode = "8906660000001"
        prod = db_service.get_product_by_barcode(db_session, bcode)
        if not prod:
            db_service.upsert_canonical_product(
                db_session,
                _make_canonical(bcode, "Phase6 Local Biscuit", calories=400, sugar=5, fat=8, salt=0.3),
            )
            db_session.commit()

        initial_history_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

        res = client.get(f"/product/{bcode}", headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()

        assert data["product"]["barcode"] == bcode
        assert data["product"]["name"] == "Phase6 Local Biscuit"
        assert data["analysis"]["health_score"] is not None
        assert data["decision"]["final_decision"] in ("SAFE", "MODERATE", "AVOID")
        assert data["assessment_status"] == "ASSESSABLE"
        assert "overall_confidence" in data

        # CRITICAL: Verify zero history records were written
        after_history_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
        assert after_history_count == initial_history_count

    def test_local_db_miss_off_success(self, client, auth_user, db_session):
        """Product absent locally is resolved via OpenFoodFacts client, cached, and returned without scan history."""
        bcode = "8901000000002"
        # Ensure not in DB
        existing = db_session.query(Product).filter(Product.barcode == bcode).first()
        if existing:
            db_session.delete(existing)
            db_session.commit()

        mock_canonical = _make_canonical(bcode, "Phase6 Remote OFF Cereal", calories=380, sugar=12, fat=4, salt=0.8)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=mock_canonical):
            res = client.get(f"/product/{bcode}", headers=auth_user["headers"])

        assert res.status_code == 200
        data = res.json()
        assert data["product"]["barcode"] == bcode
        assert data["product"]["name"] == "Phase6 Remote OFF Cereal"
        assert data["assessment_status"] == "ASSESSABLE"

    def test_truly_missing_product_returns_404(self, client, auth_user):
        """A barcode genuinely not found in DB, OFF, or CSV returns 404."""
        bcode = "9999999999999"
        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=None):
            res = client.get(f"/product/{bcode}", headers=auth_user["headers"])
        assert res.status_code == 404
        assert "product not found" in res.json()["detail"].lower()

    def test_invalid_barcode_returns_400(self, client, auth_user):
        """Malformed or short barcodes return 400."""
        res = client.get("/product/123", headers=auth_user["headers"])
        assert res.status_code == 400
        assert "invalid barcode" in res.json()["detail"].lower()

    def test_partial_nutrition_returns_none_score_and_range(self, client, auth_user):
        """Product with missing mandatory sugar returns PARTIALLY_ASSESSABLE with health_score=None and bounded score_range."""
        bcode = "8901000000003"
        partial_canonical = _make_canonical(bcode, "Phase6 Partial Oats", calories=350, sugar=None, fat=6, salt=0.1)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=partial_canonical):
            res = client.get(f"/product/{bcode}", headers=auth_user["headers"])

        assert res.status_code == 200
        data = res.json()
        assert data["assessment_status"] == "PARTIALLY_ASSESSABLE"
        assert data["analysis"]["health_score"] is None
        assert "score_range" in data
        assert data["score_range"]["min_score"] <= data["score_range"]["max_score"]
        assert 0 <= data["score_range"]["min_score"] <= 100
        assert 0 <= data["score_range"]["max_score"] <= 100


# ==============================================================================
# 2. POST /scan
# ==============================================================================

class TestScanEndpoint:
    def test_scan_creates_scan_history_record(self, client, auth_user, db_session):
        """POST /scan is the sole endpoint that inserts into ScanHistory."""
        bcode = "8901000000010"
        canonical = _make_canonical(bcode, "Phase6 Scanned Juice", calories=45, sugar=10, fat=0, salt=0.01)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=canonical):
            initial_count = db_session.query(ScanHistory).filter(
                ScanHistory.user_id == auth_user["user"].id,
                ScanHistory.barcode == bcode,
            ).count()

            res = client.post("/scan", json={"barcode": bcode}, headers=auth_user["headers"])
            assert res.status_code == 200

            after_count = db_session.query(ScanHistory).filter(
                ScanHistory.user_id == auth_user["user"].id,
                ScanHistory.barcode == bcode,
            ).count()
            assert after_count == initial_count + 1

            data = res.json()
            assert "product" in data
            assert "analysis" in data
            assert "decision" in data
            assert "recommendations" in data
            assert "daily_intake" in data
            assert "personalized_analysis" in data
            assert "explanation" in data
            assert "assessment_status" in data

    def test_scan_incomplete_product_has_none_health_score(self, client, auth_user):
        """Scanning an incomplete product returns health_score=None without fabricating a numeric score."""
        bcode = "8901000000011"
        incomplete_canonical = _make_canonical(bcode, "Phase6 Incomplete Bar", calories=200, sugar=None, fat=None, salt=None)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=incomplete_canonical):
            res = client.post("/scan", json={"barcode": bcode}, headers=auth_user["headers"])
            assert res.status_code == 200
            data = res.json()
            assert data["analysis"]["health_score"] is None
            assert data["decision"]["final_decision"] in ("INSUFFICIENT_DATA", "INCOMPLETE_DATA")
            assert data["assessment_status"] in ("UNASSESSABLE", "PARTIALLY_ASSESSABLE")


# ==============================================================================
# 3. GET /explain/{barcode}
# ==============================================================================

class TestExplainEndpoint:
    def test_explain_never_creates_scan_history(self, client, auth_user, db_session):
        """Repeated GET /explain/{barcode} requests never write to ScanHistory."""
        bcode = "8901000000020"
        canonical = _make_canonical(bcode, "Phase6 Explain Snack", calories=450, sugar=15, fat=20, salt=1.2)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=canonical):
            initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

            for _ in range(3):
                res = client.get(f"/explain/{bcode}", headers=auth_user["headers"])
                assert res.status_code == 200
                data = res.json()
                assert data["base_score"] == 100
                assert "steps" in data
                assert "threshold_info" in data
                assert "assessment_status" in data

            after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
            assert after_count == initial_count

    def test_explain_missing_product_returns_404_without_history(self, client, auth_user, db_session):
        """GET /explain on non-existent product returns 404 without writing history."""
        bcode = "9999999999998"
        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=None):
            initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
            res = client.get(f"/explain/{bcode}", headers=auth_user["headers"])
            assert res.status_code == 404
            after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
            assert after_count == initial_count


# ==============================================================================
# 4. POST /compare
# ==============================================================================

class TestCompareEndpoint:
    def test_compare_with_unseen_off_product(self, client, auth_user, db_session):
        """POST /compare can compare a product not previously stored locally by retrieving via ProductDataService."""
        bcode_a = "8901000000031"
        bcode_b = "8901000000032"

        canonical_a = _make_canonical(bcode_a, "Phase6 Compare Fried Chips", calories=550, sugar=2, fat=35, salt=1.8)
        canonical_b = _make_canonical(bcode_b, "Phase6 Compare Roasted Makhana", calories=320, sugar=1, fat=3, salt=0.3)

        def mock_get(db, barcode, **kwargs):
            if barcode == bcode_a:
                return canonical_a
            if barcode == bcode_b:
                return canonical_b
            return None

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", side_effect=mock_get):
            initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

            res = client.post(
                "/compare",
                json={"product_a": bcode_a, "product_b": bcode_b},
                headers=auth_user["headers"],
            )
            assert res.status_code == 200
            data = res.json()

            assert data["product_a"]["name"] == "Phase6 Compare Fried Chips"
            assert data["product_b"]["name"] == "Phase6 Compare Roasted Makhana"
            assert data["healthier_product"] == "Phase6 Compare Roasted Makhana"
            assert len(data["reasons"]) > 0
            assert "assessment_status" in data["product_a"]
            assert "assessment_status" in data["product_b"]

            # CRITICAL: Verify zero history records were written
            after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
            assert after_count == initial_count

    def test_compare_genuinely_missing_returns_404(self, client, auth_user):
        """POST /compare returns 404 when one or both products cannot be resolved."""
        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=None):
            res = client.post(
                "/compare",
                json={"product_a": "9999999999991", "product_b": "9999999999992"},
                headers=auth_user["headers"],
            )
            assert res.status_code == 404

    def test_compare_incomplete_product_never_fabricates_scores(self, client, auth_user):
        """POST /compare with an unassessable product sets health_score to None and does not invent winner."""
        bcode_a = "8901000000033"
        bcode_b = "8901000000034"
        unassessable_a = _make_canonical(bcode_a, "Phase6 Incomplete A", calories=None, sugar=None, fat=None, salt=None)
        canonical_b = _make_canonical(bcode_b, "Phase6 Assessable B", calories=250, sugar=2, fat=4, salt=0.3)

        def mock_get(db, barcode, **kwargs):
            if barcode == bcode_a:
                return unassessable_a
            if barcode == bcode_b:
                return canonical_b
            return None

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", side_effect=mock_get):
            res = client.post(
                "/compare",
                json={"product_a": bcode_a, "product_b": bcode_b},
                headers=auth_user["headers"],
            )
            assert res.status_code == 200
            data = res.json()
            assert data["product_a"]["health_score"] is None
            assert data["product_a"]["assessment_status"] == "UNASSESSABLE"
            assert data["product_b"]["health_score"] is not None


# ==============================================================================
# 5. POST /analyze
# ==============================================================================

class TestAnalyzeEndpoint:
    def test_analyze_complete_profile(self, client, auth_user, db_session):
        """POST /analyze with complete nutrition calculates deterministic health score."""
        initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

        payload = {
            "product_name": "Phase6 Complete Muesli",
            "calories": 360,
            "sugar": 8,
            "fat": 5,
            "saturated_fat": 1,
            "salt": 0.2,
            "protein": 11,
            "fiber": 9,
            "carbs": 60,
            "ingredients": "Rolled oats, almonds, raisins, chia seeds",
        }
        res = client.post("/analyze", json=payload, headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()

        assert data["analysis"]["health_score"] is not None
        assert data["decision"]["final_decision"] in ("SAFE", "MODERATE")
        assert data["assessment_status"] == "ASSESSABLE"
        assert "score_range" not in data  # Complete profile has exact score, no range needed

        after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
        assert after_count == initial_count

    def test_analyze_partial_profile_has_none_score_and_range(self, client, auth_user):
        """POST /analyze with missing mandatory fat returns PARTIALLY_ASSESSABLE with health_score=None."""
        payload = {
            "product_name": "Phase6 Partial Drink",
            "calories": 150,
            "sugar": 22,
            "fat": None,  # Missing mandatory
            "salt": 0.1,
        }
        res = client.post("/analyze", json=payload, headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()

        assert data["assessment_status"] == "PARTIALLY_ASSESSABLE"
        assert data["analysis"]["health_score"] is None
        assert "score_range" in data
        assert data["score_range"]["min_score"] <= data["score_range"]["max_score"]

    def test_analyze_explicit_zero_versus_none(self, client, auth_user):
        """sugar=0.0 receives no penalty, whereas sugar=None is missing disclosure."""
        # Product 1: sugar=0.0 (explicit zero sugar)
        res_zero = client.post(
            "/analyze",
            json={"product_name": "Zero Sugar Snack", "calories": 100, "sugar": 0.0, "fat": 1.0, "salt": 0.1},
            headers=auth_user["headers"],
        )
        assert res_zero.status_code == 200
        data_zero = res_zero.json()
        assert data_zero["assessment_status"] == "ASSESSABLE"
        assert data_zero["analysis"]["health_score"] is not None
        assert data_zero["analysis"]["health_score"] >= 90

        # Product 2: sugar=None (missing mandatory)
        res_none = client.post(
            "/analyze",
            json={"product_name": "Missing Sugar Snack", "calories": 100, "sugar": None, "fat": 1.0, "salt": 0.1},
            headers=auth_user["headers"],
        )
        assert res_none.status_code == 200
        data_none = res_none.json()
        assert data_none["assessment_status"] == "PARTIALLY_ASSESSABLE"
        assert data_none["analysis"]["health_score"] is None


# ==============================================================================
# 6. POST /alternatives & GET /alternatives/{barcode}
# ==============================================================================

class TestAlternativesEndpoint:
    def test_alternatives_endpoint_no_scan_history(self, client, auth_user, db_session):
        """Querying alternatives never creates a ScanHistory entry."""
        initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

        res = client.post(
            "/alternatives",
            json={"product_name": "Fried Potato Chips", "nutrition": {"calories": 550, "fat": 35, "sugar": 2, "salt": 1.5}},
            headers=auth_user["headers"],
        )
        assert res.status_code == 200
        data = res.json()
        assert "alternatives" in data

        after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
        assert after_count == initial_count

    def test_get_alternatives_by_barcode_resolves_via_pds(self, client, auth_user):
        """GET /alternatives/{barcode} resolves product via ProductDataService without history logging."""
        bcode = "8901000000040"
        canonical = _make_canonical(bcode, "Phase6 Unhealthy Cookies", calories=480, sugar=30, fat=22, salt=0.8)

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=canonical):
            res = client.get(f"/alternatives/{bcode}", headers=auth_user["headers"])
            assert res.status_code == 200
            data = res.json()
            assert data["product_name"] == "Phase6 Unhealthy Cookies"
            assert data["barcode"] == bcode


# ==============================================================================
# 7. GET /search
# ==============================================================================

class TestSearchEndpoint:
    def test_search_endpoint_returns_matches_without_history(self, client, auth_user, db_session):
        """GET /search returns results without writing ScanHistory."""
        initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

        res = client.get("/search?query=biscuit&limit=5", headers=auth_user["headers"])
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
        assert after_count == initial_count


# ==============================================================================
# 8. POST /chat
# ==============================================================================

class TestChatEndpoint:
    def test_chat_grounds_facts_and_prevents_override(self, client, auth_user, db_session):
        """POST /chat refuses to let user override or recalculate the official health score."""
        initial_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()

        # Query asking assistant to override or recalculate the score
        res = client.post(
            "/chat",
            json={
                "message": "Can you recalculate the health score to be 95 instead of 40?",
                "product_context": {"name": "Test Snack", "health_score": 40, "final_decision": "AVOID"},
            },
            headers=auth_user["headers"],
        )
        assert res.status_code == 200
        data = res.json()
        assert "answer" in data
        # Assistant must explain or refuse override
        assert "official" in data["answer"].lower() or "calculated" in data["answer"].lower() or "pramaan" in data["answer"].lower()

        after_count = db_session.query(ScanHistory).filter(ScanHistory.user_id == auth_user["user"].id).count()
        assert after_count == initial_count


# ==============================================================================
# 9. Cross-Endpoint Scoring Consistency for Benchmark Products
# ==============================================================================

class TestBenchmarkCrossEndpointConsistency:
    """Validate that identical product profiles yield identical scores across endpoints."""

    @pytest.mark.parametrize(
        "barcode,name,calories,fat,sat_fat,trans_fat,sugar,salt,protein,fiber,carbs",
        [
            ("8901058851234", "Maggi 2-Minute Noodles", 427.0, 15.7, 6.8, 0.1, 2.5, 2.8, 8.0, 3.5, 63.5),
            ("8901719101234", "Parle-G Original Gluco Biscuits", 454.0, 13.0, 6.5, 0.0, 26.5, 0.7, 7.0, 1.5, 77.0),
            ("8901262011234", "Amul Butter Pasteurized", 722.0, 80.0, 51.0, 3.0, 0.0, 2.5, 0.0, 0.0, 0.0),
            ("8901491101234", "Kurkure Masala Munch", 561.0, 34.5, 14.5, 0.1, 2.0, 1.9, 5.8, 1.8, 56.5),
            ("5449000000996", "Coca-Cola Original", 44.0, 0.0, 0.0, 0.0, 10.6, 0.0, 0.0, 0.0, 10.6),
        ],
    )
    def test_benchmark_products_identical_across_endpoints(
        self,
        client,
        auth_user,
        barcode,
        name,
        calories,
        fat,
        sat_fat,
        trans_fat,
        sugar,
        salt,
        protein,
        fiber,
        carbs,
    ):
        """Maggi, Parle-G, Amul Butter, Kurkure, and Coke must have identical health_score and decision across /product, /analyze, and /explain."""
        canonical = _make_canonical(
            barcode=barcode,
            name=name,
            calories=calories,
            fat=fat,
            saturated_fat=sat_fat,
            trans_fat=trans_fat,
            carbs=carbs,
            sugar=sugar,
            protein=protein,
            fiber=fiber,
            salt=salt,
        )

        with patch.object(get_default_product_data_service(), "get_product_by_barcode", return_value=canonical):
            # 1. GET /product/{barcode}
            res_prod = client.get(f"/product/{barcode}", headers=auth_user["headers"])
            assert res_prod.status_code == 200
            prod_data = res_prod.json()
            score_prod = prod_data["analysis"]["health_score"]
            dec_prod = prod_data["decision"]["final_decision"]
            status_prod = prod_data["assessment_status"]

            # 2. POST /analyze
            analyze_payload = {
                "product_name": name,
                "calories": calories,
                "fat": fat,
                "saturated_fat": sat_fat,
                "trans_fat": trans_fat,
                "sugar": sugar,
                "salt": salt,
                "protein": protein,
                "fiber": fiber,
                "carbs": carbs,
            }
            res_analyze = client.post("/analyze", json=analyze_payload, headers=auth_user["headers"])
            assert res_analyze.status_code == 200
            analyze_data = res_analyze.json()
            score_analyze = analyze_data["analysis"]["health_score"]
            dec_analyze = analyze_data["decision"]["final_decision"]
            status_analyze = analyze_data["assessment_status"]

            # 3. GET /explain/{barcode}
            res_explain = client.get(f"/explain/{barcode}", headers=auth_user["headers"])
            assert res_explain.status_code == 200
            explain_data = res_explain.json()
            score_explain = explain_data["final_score"]
            dec_explain = explain_data["final_decision"]
            status_explain = explain_data["assessment_status"]

            # CRITICAL: Assert cross-endpoint consistency
            assert score_prod == score_analyze == score_explain, (
                f"Mismatch in health_score for {name}: /product={score_prod}, /analyze={score_analyze}, /explain={score_explain}"
            )
            assert dec_prod == dec_analyze == dec_explain, (
                f"Mismatch in decision for {name}: /product={dec_prod}, /analyze={dec_analyze}, /explain={dec_explain}"
            )
            assert status_prod == status_analyze == status_explain, (
                f"Mismatch in assessment_status for {name}: /product={status_prod}, /analyze={status_analyze}, /explain={status_explain}"
            )
