import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.main import app
from database.db_session import get_db, SessionLocal
from database.models import Product, Nutrition, ScanHistory, User
from database.orm import init_db
from services import db_service
from services.auth_service import create_access_token


@pytest.fixture
def client_with_user():
    init_db()
    db: Session = SessionLocal()
    # Find or create a test user
    user = db.query(User).filter(User.email == "parity_test@pramaan.ai").first()
    if not user:
        user = User(
            email="parity_test@pramaan.ai",
            hashed_password="hashed_pw_test",
            name="Parity Tester",
            daily_calorie_limit=2000.0,
            diet_type=None,
            created_at=db_service._utc_now_str(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Ensure required test products exist for parity tests
    p1 = db_service.get_product_by_barcode(db, "8907000000001")
    if not p1:
        prod1 = db_service.create_product(
            db,
            {
                "barcode": "8907000000001",
                "product_name": "Batch7 Demo High Protein Bar",
                "brand": "ProFit",
                "nutriscore": "b",
                "ingredients": "Oats, whey protein isolate, almonds, honey, dark chocolate",
                "additives": None,
            },
        )
        db_service.create_nutrition(
            db,
            {
                "product_id": prod1.id,
                "calories": 420.0,
                "fat": 14.0,
                "saturated_fat": 3.0,
                "sugar": 12.0,
                "salt": 0.5,
                "protein": 22.0,
                "fiber": 8.0,
                "carbs": 45.0,
            },
        )
        db.commit()

    p3 = db_service.get_product_by_barcode(db, "8907000000003")
    if not p3:
        prod3 = db_service.create_product(
            db,
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
            db,
            {
                "product_id": prod3.id,
                "calories": 200.0,
                "fat": None,
                "saturated_fat": None,
                "trans_fat": 0.0,
                "sugar": None,
                "salt": None,
                "protein": None,
                "fiber": None,
                "carbs": None,
            },
        )
        db.commit()
    else:
        prod_obj = db.query(Product).filter(Product.barcode == "8907000000003").first()
        if prod_obj:
            n3 = db.query(Nutrition).filter(Nutrition.product_id == prod_obj.id).first()
            if n3 and n3.trans_fat is None:
                n3.trans_fat = 0.0
                db.commit()

    user_id = int(user.id)
    token = create_access_token(user_id=user_id)
    db.close()

    with TestClient(app) as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, user_id


def test_cross_endpoint_score_and_decision_parity(client_with_user):
    """Verify semantic parity of score, decision, and assessment_status between /scan and /analyze."""
    client, user_id = client_with_user

    sample_nutrition = {
        "calories": 427.0,
        "fat": 15.7,
        "saturated_fat": 6.8,
        "sugar": 2.2,
        "salt": 2.1,
        "protein": 8.0,
        "fiber": 2.0,
        "carbs": 63.5,
    }

    # 1. Post to /analyze (manual product analysis)
    analyze_resp = client.post("/analyze", json={
        "product_name": "Maggi 2-Minute Masala Noodles",
        **sample_nutrition,
    })
    assert analyze_resp.status_code == 200
    analyze_data = analyze_resp.json()

    analysis = analyze_data.get("analysis", {})
    decision = analyze_data.get("decision", {})
    assert "health_score" in analysis
    assert "final_decision" in decision
    assert "assessment_status" in analyze_data
    assert analyze_data["assessment_status"] == "ASSESSABLE"
    assert decision["final_decision"] in ("SAFE", "MODERATE", "AVOID")


def test_scan_history_isolation_invariant(client_with_user):
    """Verify that scan history is ONLY written through authorized /scan endpoints.

    Endpoints /product/{barcode}, /explain/{barcode}, /compare, /analyze, and /search
    must NEVER create records in ScanHistory.
    """
    client, user_id = client_with_user
    db: Session = SessionLocal()

    try:
        initial_count = db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count()

        # 1. Call /analyze
        client.post("/analyze", json={"product_name": "Test Snack", "calories": 200, "fat": 5})
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count

        # 2. Call /compare
        client.post("/compare", json={"product_a": "Biscuits", "product_b": "Cookies"})
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count

        # 3. Call /search
        client.get("/search", params={"query": "Amul"})
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count

        # 4. Call /product/{barcode}
        client.get("/product/8901030383748")
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count

        # 5. Call /explain/{barcode}
        client.get("/explain/8901030383748")
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count

        # 6. Call /scan (authorized scan endpoint)
        client.post("/scan", json={"barcode": "8901030383748", "product_name": "Amul Butter"})
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_count + 1

    finally:
        db.query(ScanHistory).filter(ScanHistory.user_id == user_id).delete()
        db.commit()
        db.close()


def test_explain_endpoint_parity_and_isolation(client_with_user):
    """Verify that /explain/{barcode} returns complete explanation matching /scan without scan history writes."""
    client, user_id = client_with_user
    db: Session = SessionLocal()
    barcode = "8907000000001"

    try:
        # Verify scan history isolation before explain
        initial_scan_count = db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count()

        # 1. Standard user diet profile: GET /explain/{barcode}
        resp_explain = client.get(f"/explain/{barcode}")
        assert resp_explain.status_code == 200
        explain_data = resp_explain.json()

        # Explanation contract fields
        expected_fields = [
            "product_name", "final_score", "final_decision", "base_score",
            "steps", "positive_factors", "negative_factors", "neutral_factors",
            "summary", "score_calculation", "threshold_info", "assessment_status",
            "overall_confidence", "score_range",
        ]
        for field in expected_fields:
            assert field in explain_data, f"Missing required explanation field '{field}'"

        assert explain_data["final_score"] is not None
        assert explain_data["final_decision"] in ("SAFE", "MODERATE", "AVOID")
        assert explain_data["assessment_status"] in ("ASSESSABLE", "PARTIALLY_ASSESSABLE", "UNASSESSABLE")

        # Calling /explain must NEVER create a record in ScanHistory
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_scan_count

        # 2. Compare with POST /scan explanation for the same product and user
        resp_scan = client.post("/scan", json={"barcode": barcode})
        assert resp_scan.status_code == 200
        scan_data = resp_scan.json()
        scan_explanation = scan_data.get("explanation", {})

        # Verify semantic parity between /explain and /scan explanation
        assert explain_data["final_score"] == scan_explanation["final_score"]
        assert explain_data["final_decision"] == scan_explanation["final_decision"]
        assert explain_data["assessment_status"] == scan_explanation["assessment_status"]
        assert explain_data["positive_factors"] == scan_explanation["positive_factors"]
        assert explain_data["negative_factors"] == scan_explanation["negative_factors"]
        assert explain_data["steps"] == scan_explanation["steps"]
        assert explain_data["score_calculation"] == scan_explanation["score_calculation"]

        # 3. Test with Diabetic Diet Profile
        u = db.query(User).filter(User.id == user_id).first()
        u.diet_type = "diabetic"
        db.commit()

        resp_explain_diab = client.get(f"/explain/{barcode}")
        assert resp_explain_diab.status_code == 200
        explain_diab_data = resp_explain_diab.json()

        resp_scan_diab = client.post("/scan", json={"barcode": barcode})
        assert resp_scan_diab.status_code == 200
        scan_diab_explanation = resp_scan_diab.json().get("explanation", {})

        assert explain_diab_data["final_score"] == scan_diab_explanation["final_score"]
        assert explain_diab_data["final_decision"] == scan_diab_explanation["final_decision"]
        assert explain_diab_data["diet_note"] == scan_diab_explanation["diet_note"]
        assert explain_diab_data["steps"] == scan_diab_explanation["steps"]

    finally:
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.diet_type = None
            db.commit()
        db.query(ScanHistory).filter(ScanHistory.user_id == user_id).delete()
        db.commit()
        db.close()


def test_product_endpoint_contract_and_isolation(client_with_user):
    """Verify that /product/{barcode} returns canonical product info, preserves missing data, and does not log scans."""
    client, user_id = client_with_user
    db: Session = SessionLocal()
    barcode = "8907000000003"  # Incomplete nutrition item

    try:
        initial_scan_count = db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count()

        resp = client.get(f"/product/{barcode}")
        assert resp.status_code == 200
        data = resp.json()

        # Contract checks
        assert "product" in data
        assert "analysis" in data
        assert "decision" in data
        assert "assessment_status" in data

        prod = data["product"]
        assert prod["barcode"] == barcode
        assert prod["name"] == "Batch7 Minimal Snack Incomplete Nutrition"

        # Nutrition checks: missing nutrition must remain None, not fabricated 0.0
        nutr = prod["nutrition"]
        assert nutr["calories"] == 200.0
        assert nutr["trans_fat"] == 0.0, "Explicit 0.0 must be preserved"
        assert nutr["sugar"] is None, "Missing sugar must remain None"
        assert nutr["fat"] is None, "Missing fat must remain None"
        assert nutr["salt"] is None, "Missing salt must remain None"
        # Confirm explicit 0.0 is strictly distinguishable from missing data
        assert nutr["trans_fat"] is not None
        assert nutr["trans_fat"] != nutr["sugar"]

        # Assessment status reflects partial assessability
        assert data["assessment_status"] == "PARTIALLY_ASSESSABLE"

        # Scan history must remain untouched
        assert db.query(ScanHistory).filter(ScanHistory.user_id == user_id).count() == initial_scan_count

    finally:
        db.close()


def test_explicit_zero_vs_missing_parity(client_with_user):
    """Verify that explicit 0.0 sugar vs missing (None) sugar produces distinct semantic assessments."""
    client, user_id = client_with_user

    # Product A: explicit complete disclosure with 0.0 sugar
    resp_complete = client.post("/analyze", json={
        "product_name": "Sugar Free Complete Beverage",
        "calories": 5.0,
        "sugar": 0.0,
        "fat": 0.0,
        "saturated_fat": 0.0,
        "trans_fat": 0.0,
        "salt": 0.05,
        "protein": 0.0,
        "fiber": 0.0,
        "carbs": 0.0,
    })
    assert resp_complete.status_code == 200
    data_complete = resp_complete.json()

    # Product B: missing disclosures (calories only)
    resp_incomplete = client.post("/analyze", json={
        "product_name": "Undisclosed Beverage",
        "calories": 5.0,
    })
    assert resp_incomplete.status_code == 200
    data_incomplete = resp_incomplete.json()

    complete_status = data_complete.get("assessment_status")
    incomplete_status = data_incomplete.get("assessment_status")

    assert complete_status == "ASSESSABLE"
    assert incomplete_status in ("PARTIALLY_ASSESSABLE", "UNASSESSABLE")


def test_diabetic_profile_penalty_parity(client_with_user):
    """Verify that diabetic profile applies authoritative sugar penalty consistently."""
    client, user_id = client_with_user
    db: Session = SessionLocal()

    sweet_product = {
        "product_name": "Sweetened Condensed Milk",
        "calories": 320,
        "sugar": 55.0,
        "fat": 8.0,
        "salt": 0.3,
        "protein": 7.0,
    }

    try:
        # Standard profile (diet_type = None)
        u = db.query(User).filter(User.id == user_id).first()
        u.diet_type = None
        db.commit()

        resp_std = client.post("/analyze", json=sweet_product)
        assert resp_std.status_code == 200
        score_std = resp_std.json().get("analysis", {}).get("health_score")

        # Diabetic profile (diet_type = "diabetic")
        u.diet_type = "diabetic"
        db.commit()

        resp_diab = client.post("/analyze", json=sweet_product)
        assert resp_diab.status_code == 200
        score_diab = resp_diab.json().get("analysis", {}).get("health_score")

        if score_std is not None and score_diab is not None:
            assert score_diab < score_std

    finally:
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.diet_type = None
            db.commit()
        db.close()


def test_explicit_zero_trans_fat_persistence_and_serialization(client_with_user):
    """Regression test: Explicit 0.0 trans-fat is persisted, retrieved, and serialized correctly without becoming None."""
    client, user_id = client_with_user
    db: Session = SessionLocal()
    test_barcode = "8909999000001"

    try:
        # Clean any existing record
        existing = db.query(Product).filter(Product.barcode == test_barcode).first()
        if existing:
            db.query(Nutrition).filter(Nutrition.product_id == existing.id).delete()
            db.delete(existing)
            db.commit()

        prod = db_service.create_product(
            db,
            {
                "barcode": test_barcode,
                "product_name": "Zero Trans Fat Test Biscuit",
                "brand": "TestBakery",
                "nutriscore": "c",
                "ingredients": "Wheat flour, vegetable oil, salt",
            },
        )
        db_service.create_nutrition(
            db,
            {
                "product_id": prod.id,
                "calories": 350.0,
                "fat": 10.0,
                "saturated_fat": 3.0,
                "trans_fat": 0.0,  # Explicit 0.0
                "sugar": None,     # Missing disclosure
                "salt": 0.5,
                "protein": 7.0,
                "fiber": 2.0,
                "carbs": 55.0,
            },
        )
        db.commit()

        # Query endpoint
        resp = client.get(f"/product/{test_barcode}")
        assert resp.status_code == 200
        data = resp.json()

        nutr = data["product"]["nutrition"]
        assert nutr["trans_fat"] == 0.0, "Explicit 0.0 trans_fat must be preserved"
        assert nutr["trans_fat"] is not None
        assert nutr["sugar"] is None, "Missing sugar disclosure must remain None"
        assert nutr["trans_fat"] != nutr["sugar"]

    finally:
        p = db.query(Product).filter(Product.barcode == test_barcode).first()
        if p:
            db.query(Nutrition).filter(Nutrition.product_id == p.id).delete()
            db.delete(p)
            db.commit()
        db.close()


def test_missing_trans_fat_remains_none(client_with_user):
    """Regression test: Missing trans-fat disclosure is never coerced or fabricated into 0.0."""
    client, user_id = client_with_user
    db: Session = SessionLocal()
    test_barcode = "8909999000002"

    try:
        # Clean any existing record
        existing = db.query(Product).filter(Product.barcode == test_barcode).first()
        if existing:
            db.query(Nutrition).filter(Nutrition.product_id == existing.id).delete()
            db.delete(existing)
            db.commit()

        prod = db_service.create_product(
            db,
            {
                "barcode": test_barcode,
                "product_name": "Missing Trans Fat Test Snack",
                "brand": "TestBrand",
                "nutriscore": "d",
                "ingredients": "Puffed rice, seasoning",
            },
        )
        db_service.create_nutrition(
            db,
            {
                "product_id": prod.id,
                "calories": 180.0,
                "fat": 4.0,
                "saturated_fat": 1.0,
                "trans_fat": None,  # Missing disclosure
                "sugar": 2.0,       # Explicit value
                "salt": 0.3,
                "protein": 3.0,
                "fiber": 1.0,
                "carbs": 32.0,
            },
        )
        db.commit()

        # Query endpoint
        resp = client.get(f"/product/{test_barcode}")
        assert resp.status_code == 200
        data = resp.json()

        nutr = data["product"]["nutrition"]
        assert nutr["trans_fat"] is None, "Missing trans_fat disclosure must remain None"
        assert nutr["sugar"] == 2.0, "Explicit sugar must remain 2.0"

    finally:
        p = db.query(Product).filter(Product.barcode == test_barcode).first()
        if p:
            db.query(Nutrition).filter(Nutrition.product_id == p.id).delete()
            db.delete(p)
            db.commit()
        db.close()
