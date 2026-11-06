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

os.environ["SECRET_KEY"] = "test-secret-key-candidate-selection-regression"

from database.orm import SessionLocal
from database.models import User, Product, Nutrition
from services import db_service
from services.auth_service import hash_password, create_access_token
from api.main import app


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def auth_headers(db_session):
    email = "candidate_audit_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Audit User",
            hashed_password=hash_password("AuditPass123"),
            daily_calorie_limit=2000,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {"Authorization": f"Bearer {token}"}


SYNTHETIC_CANDIDATE_PREFIX = "8909998"


@pytest.fixture
def seed_synthetic_candidates(db_session):
    """Seed 420 deterministic synthetic products to verify that candidate selection
    and fuzzy search work without assuming existing database records.

    Cleans up all seeded synthetic products after test execution.
    """
    # 1. Clean up any leftover synthetic records with this prefix
    db_session.query(Product).filter(Product.barcode.like(f"{SYNTHETIC_CANDIDATE_PREFIX}%")).delete(synchronize_session=False)
    db_session.commit()

    # 2. Seed 420 synthetic products with unique barcodes and names, newer than 1990
    seeded_products = [
        Product(
            barcode=f"{SYNTHETIC_CANDIDATE_PREFIX}{i:06d}",
            product_name=f"Synthetic Candidate Food Product {i:04d}",
            brand="SyntheticBrand",
            nutriscore="a" if i % 2 == 0 else "b",
            ingredients="Synthetic oats, water, salt",
            additives=None,
            created_at=f"2026-02-01 {i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}",
        )
        for i in range(1, 421)
    ]
    db_session.bulk_save_objects(seeded_products)
    db_session.commit()

    yield seeded_products

    # 3. Clean up synthetic records after test execution
    db_session.query(Product).filter(Product.barcode.like(f"{SYNTHETIC_CANDIDATE_PREFIX}%")).delete(synchronize_session=False)
    db_session.commit()


def test_get_product_name_candidates_unlimited_and_limited(db_session, seed_synthetic_candidates):
    """Verify get_product_name_candidates returns all products when limit is None, and respects limit when given."""
    total_products = db_session.query(Product).filter(Product.product_name.is_not(None)).count()
    assert total_products >= 420, f"Expected at least 420 products in DB to test truncation, got {total_products}"

    # Unlimited: must return all product names, not capped at 400
    all_candidates = db_service.get_product_name_candidates(db_session, limit=None)
    assert len(all_candidates) == total_products

    # Verify every seeded candidate is present in the returned candidates list
    seeded_names = {p.product_name for p in seed_synthetic_candidates}
    candidate_name_set = set(all_candidates)
    assert seeded_names.issubset(candidate_name_set), "All seeded candidate names must be returned when limit=None"

    # Explicit limit: must respect given limit
    ten_candidates = db_service.get_product_name_candidates(db_session, limit=10)
    assert len(ten_candidates) == 10


def test_exact_case_insensitive_match(db_session):
    """Verify exact match returns immediately with 100% similarity regardless of casing or surrounding whitespace."""
    # Seed a known product
    barcode = "8909999000001"
    existing = db_session.query(Product).filter(Product.barcode == barcode).first()
    if not existing:
        prod = db_service.create_product(
            db_session,
            {
                "barcode": barcode,
                "product_name": "Task11 Crisp Honey Granola",
                "brand": "Pramaan Farms",
                "nutriscore": "a",
                "created_at": "2026-01-01 00:00:00",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod.id,
                "calories": 380.0,
                "sugar": 8.0,
                "salt": 0.2,
                "fat": 6.0,
                "fiber": 8.0,
                "protein": 12.0,
            },
        )
        db_session.commit()

    # Exact lowercase with whitespace
    result = db_service.get_product_by_name_fuzzy(db_session, "  task11 crisp honey granola  ")
    assert result is not None
    assert result["product_name"] == "Task11 Crisp Honey Granola"
    assert result["barcode"] == barcode
    assert result["_match"]["similarity"] == 100.0
    assert result["brand"] == "Pramaan Farms"


def test_sql_like_filtering_with_wildcard_escaping(db_session):
    """Verify SQL LIKE finds substring match and safely escapes %, _, / without SQL injection or syntax error."""
    # Seed product with special characters in name
    barcode = "8909999000002"
    existing = db_session.query(Product).filter(Product.barcode == barcode).first()
    if not existing:
        prod = db_service.create_product(
            db_session,
            {
                "barcode": barcode,
                "product_name": "100% Whole Wheat/Rye Crisps (Special_Edition)",
                "brand": "Artisan Bakers",
                "nutriscore": "b",
                "created_at": "2026-01-01 00:00:00",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod.id,
                "calories": 310.0,
                "sugar": 2.0,
                "salt": 0.5,
                "fat": 3.0,
                "fiber": 10.0,
                "protein": 11.0,
            },
        )
        db_session.commit()

    # Substring search containing %, /, and _
    result = db_service.get_product_by_name_fuzzy(db_session, "100% Whole Wheat/Rye")
    assert result is not None
    assert "Whole Wheat/Rye" in result["product_name"]
    assert result["barcode"] == barcode

    # Substring search containing Special_Edition
    res_underscore = db_service.get_product_by_name_fuzzy(db_session, "Special_Edition")
    assert res_underscore is not None
    assert res_underscore["barcode"] == barcode


def test_product_outside_previous_400_candidate_limit(db_session, auth_headers, seed_synthetic_candidates):
    """Verify that an older product created with an early timestamp is resolved via get_product_by_name_fuzzy and /compare
    even when preceded by >= 400 newer products."""
    barcode = "8909999000003"
    target_name = "Ancient Recipe Rolled Oats 1990"

    # Seed target product with ancient timestamp (1990-01-01)
    prod = db_service.create_product(
        db_session,
        {
            "barcode": barcode,
            "product_name": target_name,
            "brand": "Heritage Mill",
            "nutriscore": "a",
            "created_at": "1990-01-01 00:00:00",
        },
    )
    db_service.create_nutrition(
        db_session,
        {
            "product_id": prod.id,
            "calories": 360.0,
            "sugar": 1.0,
            "salt": 0.05,
            "fat": 5.0,
            "fiber": 9.0,
            "protein": 13.0,
        },
    )

    # Also ensure counterpart product exists for /compare
    comp_barcode = "8909999000001"
    comp_prod = db_service.create_product(
        db_session,
        {
            "barcode": comp_barcode,
            "product_name": "Task11 Crisp Honey Granola",
            "brand": "Pramaan Farms",
            "nutriscore": "a",
            "created_at": "2026-01-01 00:00:00",
        },
    )
    db_service.create_nutrition(
        db_session,
        {
            "product_id": comp_prod.id,
            "calories": 380.0,
            "sugar": 8.0,
            "salt": 0.2,
            "fat": 6.0,
            "fiber": 8.0,
            "protein": 12.0,
        },
    )
    db_session.commit()

    try:
        # Verify that there are >= 400 newer products (seeded by fixture)
        newer_count = (
            db_session.query(Product)
            .filter(Product.created_at > "1990-01-01 00:00:00")
            .count()
        )
        assert newer_count >= 400, f"Expected >= 400 newer products, found {newer_count}"

        # 1. Direct db_service lookup
        found = db_service.get_product_by_name_fuzzy(db_session, target_name)
        assert found is not None
        assert found["barcode"] == barcode
        assert found["product_name"] == target_name

        # 2. Fuzzy variation lookup (partial / slight difference)
        fuzzy_found = db_service.get_product_by_name_fuzzy(db_session, "Ancient Recipe Rolled Oats")
        assert fuzzy_found is not None
        assert fuzzy_found["barcode"] == barcode

        # 3. Fuzzy typo lookup
        fuzzy_typo = db_service.get_product_by_name_fuzzy(db_session, "Ancient Recipe Roled Oats 1990", min_similarity=80.0)
        assert fuzzy_typo is not None
        assert fuzzy_typo["barcode"] == barcode

        # 4. End-to-end API lookup via /compare
        client = TestClient(app)
        res_compare = client.post(
            "/compare",
            json={
                "product_a": target_name,
                "product_b": "Task11 Crisp Honey Granola",
            },
            headers=auth_headers,
        )
        assert res_compare.status_code == 200, f"Compare failed with status {res_compare.status_code}: {res_compare.text}"
        data = res_compare.json()
        assert data["product_a"]["name"] == target_name
        assert "healthier_product" in data
        assert "reasons" in data

    finally:
        # Clean up target test product
        db_session.query(Nutrition).filter(Nutrition.product_id == prod.id).delete(synchronize_session=False)
        db_session.query(Product).filter(Product.id == prod.id).delete(synchronize_session=False)
        db_session.commit()


def test_fuzzy_typo_fallback_without_candidate_truncation(db_session):
    """Verify that a query with transposed letters / typo falls back to full-database fuzzy comparison."""
    barcode = "8909999000004"
    existing = db_session.query(Product).filter(Product.barcode == barcode).first()
    if not existing:
        prod = db_service.create_product(
            db_session,
            {
                "barcode": barcode,
                "product_name": "Quinoa Superfood Medley Bowl",
                "brand": "NutriMeal",
                "nutriscore": "a",
                "created_at": "1995-05-05 12:00:00",
            },
        )
        db_service.create_nutrition(
            db_session,
            {
                "product_id": prod.id,
                "calories": 250.0,
                "sugar": 2.0,
                "salt": 0.4,
                "fat": 4.0,
                "fiber": 7.0,
                "protein": 9.0,
            },
        )
        db_session.commit()

    # Query with transposed / typo: "Quinoa Superfood Medly" (misses exact and misses exact full substring)
    # rapidfuzz token_set_ratio is > 85
    found = db_service.get_product_by_name_fuzzy(db_session, "Quinoa Superfood Medly Bowl", min_similarity=80.0)
    assert found is not None
    assert found["barcode"] == barcode
    assert found["_match"]["similarity"] >= 80.0


def test_unmatched_query_returns_none(db_session):
    """Verify that a query for an unknown product cleanly returns None."""
    found = db_service.get_product_by_name_fuzzy(db_session, "TotallyZilchNonExistentProductZ9999", min_similarity=80.0)
    assert found is None
