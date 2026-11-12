from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.orm import SessionLocal, init_db
from database.models import User
from services.auth_service import hash_password, create_access_token
from services import db_service
from services.product_search import normalize_brand, search_products


@pytest.fixture(scope="module")
def client():
    init_db()
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_user():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "search_tester@example.com").first()
        if not user:
            user = User(
                email="search_tester@example.com",
                name="Search Tester",
                hashed_password=hash_password("password123"),
                daily_calorie_limit=2000,
                created_at=db_service._utc_now_str(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user_id=int(user.id))
        return {"id": user.id, "headers": {"Authorization": f"Bearer {token}"}}
    finally:
        db.close()


def test_search_exact_and_partial_product_name(client, auth_user):
    """Verify exact and partial product-name queries return expected records with nutrition data."""
    # Exact match
    res_exact = client.get("/search?query=Amul%20Butter", headers=auth_user["headers"])
    assert res_exact.status_code == 200
    exact_data = res_exact.json()
    assert isinstance(exact_data, list)
    assert len(exact_data) >= 1
    assert exact_data[0]["product_name"] == "Amul Butter"
    assert exact_data[0]["brand"] == "Amul"
    assert exact_data[0]["nutrition"]["calories"] == 717.0
    assert exact_data[0]["source"] in ("indian_dataset", "local_db")

    # Partial match
    res_part = client.get("/search?query=Butter", headers=auth_user["headers"])
    assert res_part.status_code == 200
    part_data = res_part.json()
    assert any("Butter" in p["product_name"] for p in part_data)


def test_search_brand_matching(client, auth_user):
    """Verify brand matching finds products by brand name."""
    res = client.get("/search?query=Britannia", headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert any(p.get("brand") == "Britannia" for p in data)


def test_search_ingredient_matching(client, auth_user):
    """Verify searching for ingredient keywords finds matching products."""
    res = client.get("/search?query=cocoa", headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1


def test_search_openfoodfacts_local_dataset(client, auth_user):
    """Verify local OpenFoodFacts dataset records (e.g. Lindt) are discoverable."""
    res = client.get("/search?query=Lindt", headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert any("lindt" in str(p.get("brand") or "").lower() or "lindt" in p["product_name"].lower() for p in data)


def test_search_case_insensitivity(client, auth_user):
    """Verify search is case-insensitive."""
    res_lower = client.get("/search?query=amul", headers=auth_user["headers"]).json()
    res_upper = client.get("/search?query=AMUL", headers=auth_user["headers"]).json()
    res_mixed = client.get("/search?query=AmUl", headers=auth_user["headers"]).json()

    assert len(res_lower) == len(res_upper) == len(res_mixed)
    assert res_lower[0]["product_name"] == res_upper[0]["product_name"] == res_mixed[0]["product_name"]


def test_search_pagination_limit(client, auth_user):
    """Verify limit parameter bounds result count."""
    res_5 = client.get("/search?query=a&limit=5", headers=auth_user["headers"])
    assert res_5.status_code == 200
    assert len(res_5.json()) <= 5

    res_10 = client.get("/search?query=a&limit=10", headers=auth_user["headers"])
    assert res_10.status_code == 200
    assert len(res_10.json()) <= 10


def test_search_sql_injection_safety(client, auth_user):
    """Verify SQL injection strings are safely parameterized and handled without crash."""
    malicious_inputs = [
        "' OR '1'='1",
        "'; DROP TABLE products; --",
        "\" OR \"\"=\"",
        "%'; SELECT * FROM users; --",
    ]
    for bad_query in malicious_inputs:
        res = client.get(f"/search?query={bad_query}", headers=auth_user["headers"])
        assert res.status_code == 200
        assert isinstance(res.json(), list)


def test_search_sql_wildcard_escaping(client, auth_user):
    """Verify search safely escapes SQL LIKE wildcards (% and _) without error."""
    for wildcard in ["%", "_", "%_", "100%"]:
        res = client.get(f"/search?query={wildcard}", headers=auth_user["headers"])
        assert res.status_code == 200
        assert isinstance(res.json(), list)


def test_search_unauthenticated_rejected(client):
    """Verify GET /search requires valid JWT auth."""
    res = client.get("/search?query=Amul")
    assert res.status_code == 401


def test_brand_normalization_rules():
    """Verify brand normalization preserves mixed-case and acronyms while normalizing all-caps."""
    assert normalize_brand("AMUL") == "Amul"
    assert normalize_brand("BRITANNIA") == "Britannia"
    assert normalize_brand("CADBURY") == "Cadbury"
    assert normalize_brand("KELLOGG'S") == "Kellogg's"
    assert normalize_brand("KFC") == "KFC"
    assert normalize_brand("MDH") == "MDH"
    assert normalize_brand("ITC") == "ITC"
    assert normalize_brand("M&S") == "M&S"
    assert normalize_brand("iPhone") == "iPhone"
    assert normalize_brand("McVitie's") == "McVitie's"
    assert normalize_brand("Lay's") == "Lay's"
    assert normalize_brand("Parle-G") == "Parle-G"
    assert normalize_brand("Bio Choice, WholeGrains") == "Bio Choice"
    assert normalize_brand("liebig") == "Liebig"
    assert normalize_brand(None) is None
    assert normalize_brand("   ") is None


def test_search_brand_retention_amul_butter(client, auth_user):
    """Regression test: Amul Butter retains brand 'Amul' across search results and local DB fallbacks."""
    res = client.get("/search?query=Amul%20Butter", headers=auth_user["headers"])
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1

    # Find the exact Amul Butter entry
    amul_butter = next((p for p in data if p["product_name"] == "Amul Butter"), None)
    assert amul_butter is not None, "Amul Butter must be present in search results"
    assert amul_butter["brand"] == "Amul", f"Expected brand 'Amul', got {amul_butter.get('brand')}"
    assert amul_butter["nutrition"]["calories"] in (717.0, 724.0)


def test_search_missing_brand_remains_missing_not_fabricated(client, auth_user):
    """Regression test: Unbranded products retain brand None in search results and are never fabricated."""
    from database.models import Product, Nutrition
    from services import db_service
    db = SessionLocal()
    unbranded_code = "8909999888888"
    unbranded_name = "Completely Unbranded Artisan Bread 888"

    try:
        existing = db.query(Product).filter(Product.barcode == unbranded_code).first()
        if existing:
            db.query(Nutrition).filter(Nutrition.product_id == existing.id).delete()
            db.delete(existing)
            db.commit()

        prod = db_service.create_product(
            db,
            {
                "barcode": unbranded_code,
                "product_name": unbranded_name,
                "brand": None,  # Explicitly unbranded
                "nutriscore": "b",
                "ingredients": "Flour, water, yeast, salt",
            },
        )
        db.commit()

        res = client.get(f"/search?query={unbranded_name}", headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1

        match = next((p for p in data if p["barcode"] == unbranded_code), None)
        assert match is not None, "Unbranded product must be found by name"
        assert match["brand"] is None, f"Missing brand must remain None, but got {match.get('brand')}"

    finally:
        p = db.query(Product).filter(Product.barcode == unbranded_code).first()
        if p:
            db.query(Nutrition).filter(Nutrition.product_id == p.id).delete()
            db.delete(p)
            db.commit()
        db.close()
