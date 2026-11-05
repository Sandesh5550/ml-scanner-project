from __future__ import annotations

import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

from database.orm import Base, create_database_engine
from database.models import Product, Nutrition, FoodLog, User
from services import db_service
from services.openfoodfacts_service import _normalize_product_dict
from services.ocr_service import parse_structured_ocr, process_image_ocr
from services.barcode_lookup import lookup_product
from services.claim_verification import verify_single_claim, verify_claims
from api.main import app, AnalyzeRequest, FoodLogRequest, FoodLogUpdateRequest
from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]


def _get_alembic_config(db_url: str) -> Config:
    alembic_ini = BASE_DIR / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


# ==============================================================================
# 1. ALEMBIC MIGRATION TESTS (002 -> 003, DOWNGRADE & RE-UPGRADE)
# ==============================================================================

def test_migration_002_to_003_upgrade_downgrade_reupgrade(tmp_path):
    """Verify SQLite migration from revision 002 to 003, column creation, data preservation, and downgrade."""
    test_db = tmp_path / "test_migration_003.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # 1. Upgrade to 002
    command.upgrade(cfg, "002_add_ocr_jobs_and_allergens")

    engine = create_database_engine(db_url)
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "002_add_ocr_jobs_and_allergens"

    # Seed existing rows under 002 (without saturated_fat or trans_fat)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, created_at) "
                "VALUES (1, 'test@example.com', 'hash', '2026-11-05T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO products (id, barcode, product_name, created_at) "
                "VALUES (1, '1234567890123', 'Seeded Cookie', '2026-11-05T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO nutrition (id, product_id, calories, fat, sugar, salt, protein, fiber, carbs) "
                "VALUES (1, 1, 450.0, 20.0, 25.0, 0.8, 5.0, 2.0, 60.0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO daily_food_log (id, user_id, barcode, product_name, calories, fat, sugar, salt, protein, fiber, carbs, consumed_at) "
                "VALUES (1, 1, '1234567890123', 'Seeded Cookie', 450.0, 20.0, 25.0, 0.8, 5.0, 2.0, 60.0, '2026-11-05 12:00:00')"
            )
        )

    # 2. Upgrade to 003
    command.upgrade(cfg, "003_add_saturated_and_trans_fat")

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "003_add_saturated_and_trans_fat"

    inspector = inspect(engine)
    nutr_cols = {c["name"]: c for c in inspector.get_columns("nutrition")}
    log_cols = {c["name"]: c for c in inspector.get_columns("daily_food_log")}

    assert "saturated_fat" in nutr_cols and nutr_cols["saturated_fat"]["nullable"] is True
    assert "trans_fat" in nutr_cols and nutr_cols["trans_fat"]["nullable"] is True
    assert "saturated_fat" in log_cols and log_cols["saturated_fat"]["nullable"] is True
    assert "trans_fat" in log_cols and log_cols["trans_fat"]["nullable"] is True

    # 3. Verify data preservation of existing seeded rows (new columns are NULL)
    with engine.connect() as conn:
        row_nutr = conn.execute(text("SELECT calories, fat, saturated_fat, trans_fat FROM nutrition WHERE id = 1")).first()
        assert row_nutr.calories == 450.0
        assert row_nutr.fat == 20.0
        assert row_nutr.saturated_fat is None
        assert row_nutr.trans_fat is None

        row_log = conn.execute(text("SELECT calories, fat, saturated_fat, trans_fat FROM daily_food_log WHERE id = 1")).first()
        assert row_log.calories == 450.0
        assert row_log.fat == 20.0
        assert row_log.saturated_fat is None
        assert row_log.trans_fat is None

    # 4. Insert new row with populated saturated_fat and trans_fat
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (id, barcode, product_name, created_at) "
                "VALUES (2, '9999999999999', 'Dark Chocolate', '2026-11-05T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO nutrition (id, product_id, calories, fat, saturated_fat, trans_fat) "
                "VALUES (2, 2, 550.0, 35.0, 18.5, 0.05)"
            )
        )

    with engine.connect() as conn:
        row_choc = conn.execute(text("SELECT saturated_fat, trans_fat FROM nutrition WHERE id = 2")).first()
        assert row_choc.saturated_fat == 18.5
        assert row_choc.trans_fat == 0.05

    # 5. Downgrade to 002
    command.downgrade(cfg, "002_add_ocr_jobs_and_allergens")
    inspector_down = inspect(engine)
    down_nutr_cols = {c["name"] for c in inspector_down.get_columns("nutrition")}
    down_log_cols = {c["name"] for c in inspector_down.get_columns("daily_food_log")}
    assert "saturated_fat" not in down_nutr_cols
    assert "trans_fat" not in down_nutr_cols
    assert "saturated_fat" not in down_log_cols
    assert "trans_fat" not in down_log_cols

    with engine.connect() as conn:
        rev_down = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev_down == "002_add_ocr_jobs_and_allergens"

    # 6. Re-upgrade to 003
    command.upgrade(cfg, "003_add_saturated_and_trans_fat")
    inspector_re = inspect(engine)
    re_nutr_cols = {c["name"] for c in inspector_re.get_columns("nutrition")}
    assert "saturated_fat" in re_nutr_cols
    assert "trans_fat" in re_nutr_cols


def test_postgresql_migration_003_compatibility():
    """Verify migration 003 applies cleanly against PostgreSQL if configured."""
    pg_url = os.getenv("TEST_DATABASE_URL") or os.getenv("POSTGRES_TEST_URL")
    if not pg_url or not pg_url.startswith("postgres"):
        pytest.skip("PostgreSQL test service not configured (TEST_DATABASE_URL not set).")

    cfg = _get_alembic_config(pg_url)
    command.upgrade(cfg, "003_add_saturated_and_trans_fat")
    engine = create_database_engine(pg_url)
    inspector = inspect(engine)
    nutr_cols = {c["name"] for c in inspector.get_columns("nutrition")}
    assert "saturated_fat" in nutr_cols
    assert "trans_fat" in nutr_cols


# ==============================================================================
# 2. OPENFOODFACTS EXTRACTION TESTS
# ==============================================================================

def test_openfoodfacts_extraction_all_key_variations():
    """Verify OpenFoodFacts normalizer extracts saturated and trans fats across key variations."""
    # Variation 1: standard 100g keys
    raw_product_1 = {
        "product_name": "Peanut Butter",
        "nutriments": {
            "energy-kcal_100g": 600,
            "fat_100g": 50.0,
            "saturated-fat_100g": 9.5,
            "trans-fat_100g": 0.02,
            "sugars_100g": 8.0,
        },
    }
    norm_1 = _normalize_product_dict("1111111111111", raw_product_1)
    assert norm_1["saturated_fat"] == 9.5
    assert norm_1["trans_fat"] == 0.02

    # Variation 2: underscore keys
    raw_product_2 = {
        "product_name": "Almond Butter",
        "nutriments": {
            "energy-kcal_100g": 620,
            "fat_100g": 52.0,
            "saturated_fat_100g": 4.2,
            "trans_fat_100g": 0.01,
        },
    }
    norm_2 = _normalize_product_dict("2222222222222", raw_product_2)
    assert norm_2["saturated_fat"] == 4.2
    assert norm_2["trans_fat"] == 0.01

    # Variation 3: short keys
    raw_product_3 = {
        "product_name": "Ghee",
        "nutriments": {
            "energy-kcal_100g": 900,
            "fat": 100.0,
            "saturated-fat": 65.0,
            "trans-fat": 1.2,
        },
    }
    norm_3 = _normalize_product_dict("3333333333333", raw_product_3)
    assert norm_3["saturated_fat"] == 65.0
    assert norm_3["trans_fat"] == 1.2

    # Variation 4: missing values remain None, never replaced with 0.0
    raw_product_4 = {
        "product_name": "Raw Apple",
        "nutriments": {
            "energy-kcal_100g": 52,
            "fat_100g": 0.2,
        },
    }
    norm_4 = _normalize_product_dict("4444444444444", raw_product_4)
    assert norm_4["saturated_fat"] is None
    assert norm_4["trans_fat"] is None


# ==============================================================================
# 3. OCR EXTRACTION TESTS (FLAT AND NESTED)
# ==============================================================================

def test_ocr_flat_and_nested_nutrition_output():
    """Verify OCR parser extracts saturated and trans fats into both flat scalar and nested structures."""
    sample_text = """
    NUTRITION INFORMATION
    Per 100g:
    Energy: 450 kcal
    Total Fat: 18.0 g
    Saturated Fat: 5.5 g
    Trans Fat: 0.08 g
    Carbohydrate: 65.0 g
    Total Sugars: 24.0 g
    Protein: 7.2 g
    Sodium: 320 mg
    """
    res = parse_structured_ocr(sample_text)

    # Flat top-level values
    assert res["saturated_fat"] == 5.5
    assert res["trans_fat"] == 0.08

    # Nested nutrition dictionary
    assert "saturated_fat_g" in res["nutrition"]
    assert res["nutrition"]["saturated_fat_g"]["value"] == 5.5
    assert "trans_fat_g" in res["nutrition"]
    assert res["nutrition"]["trans_fat_g"]["value"] == 0.08

    # Fallback / empty image test
    empty_res = process_image_ocr(b"")
    assert empty_res["saturated_fat"] is None
    assert empty_res["trans_fat"] is None


# ==============================================================================
# 4. DATABASE SERVICE PERSISTENCE, LOOKUP, FOOD LOGGING & UPDATES
# ==============================================================================

def test_database_persistence_and_retrieval(tmp_path):
    """Verify db_service create, get, fuzzy, food log, week/day query, and update routines."""
    db_file = tmp_path / "test_dbservice.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        # 1. Create product and nutrition
        prod = db_service.create_product(
            db,
            {
                "barcode": "8901030000000",
                "product_name": "Parle Platinum Biscuit",
                "brand": "Parle",
            },
        )
        db_service.create_nutrition(
            db,
            {
                "product_id": prod.id,
                "calories": 480.0,
                "fat": 20.0,
                "saturated_fat": 8.0,
                "trans_fat": 0.05,
                "sugar": 22.0,
                "salt": 0.5,
                "protein": 6.0,
                "fiber": 2.5,
                "carbs": 68.0,
            },
        )
        db.commit()

        # 2. Get product by barcode
        fetched = db_service.get_product_by_barcode(db, "8901030000000")
        assert fetched is not None
        assert fetched["saturated_fat"] == 8.0
        assert fetched["trans_fat"] == 0.05

        # 3. Fuzzy search product
        fuzzy = db_service.get_product_by_name_fuzzy(db, "Parle Platinum Biscuit")
        assert fuzzy is not None
        assert fuzzy["saturated_fat"] == 8.0
        assert fuzzy["trans_fat"] == 0.05

        # 4. Log food consumption
        log = db_service.log_food_consumption(
            db,
            barcode="8901030000000",
            product_name="Parle Platinum Biscuit",
            calories=480.0,
            fat=20.0,
            saturated_fat=8.0,
            trans_fat=0.05,
            sugar=22.0,
            user_id=1,
        )
        assert log.id is not None
        assert log.saturated_fat == 8.0
        assert log.trans_fat == 0.05

        # 5. Get today food logs
        today_logs = db_service.get_today_food_logs(db, user_id=1)
        assert len(today_logs) == 1
        assert today_logs[0]["saturated_fat"] == 8.0
        assert today_logs[0]["trans_fat"] == 0.05

        # 6. Get week food logs
        week_logs = db_service.get_week_food_logs(db, user_id=1)
        all_logs = [item for sublist in week_logs.values() for item in sublist]
        assert len(all_logs) == 1
        assert all_logs[0]["saturated_fat"] == 8.0
        assert all_logs[0]["trans_fat"] == 0.05

        # 7. Update food log
        updated = db_service.update_food_log(
            db,
            log_id=log.id,
            user_id=1,
            updates={"saturated_fat": 9.2, "trans_fat": 0.02},
        )
        assert updated is not None
        assert updated.saturated_fat == 9.2
        assert updated.trans_fat == 0.02


# ==============================================================================
# 5. BARCODE LOOKUP, CACHED RETRIEVAL & HEALTH SCORE PENALTY
# ==============================================================================

def test_barcode_lookup_preserves_fats_and_cached_health_score(tmp_path, monkeypatch):
    """Verify that barcode lookup extracts fats and cached lookup applies the saturated fat penalty."""
    db_file = tmp_path / "test_lookup.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)

    # Mock OpenFoodFacts fetch
    def mock_fetch(barcode, timeout=8.0):
        return {
            "barcode": barcode,
            "product_name": "High Sat Fat Snack",
            "brand": "Crispy Treats",
            "nutriscore": None,
            "ingredients": "Whole wheat flour, Oats, Water",
            "additives": None,
            "calories": 250.0,
            "fat": 20.0,
            "saturated_fat": 12.0,  # > 5.0 -> should trigger -10 penalty and "High saturated fat"
            "trans_fat": 0.05,
            "sugar": 4.0,
            "salt": 0.2,
            "protein": 6.0,
            "fiber": 4.0,
            "carbs": 30.0,
        }

    monkeypatch.setattr("services.barcode_lookup.fetch_product_by_barcode", mock_fetch)

    with Session(engine) as db:
        # First lookup: fetches from OpenFoodFacts and stores in DB
        res1 = lookup_product(db, "8909999999999")
        assert res1 is not None
        assert res1["saturated_fat"] == 12.0
        assert res1["trans_fat"] == 0.05

        # Second lookup: served directly from local database cache
        res2 = lookup_product(db, "8909999999999")
        assert res2 is not None
        assert res2["saturated_fat"] == 12.0
        assert res2["trans_fat"] == 0.05

        # Crucial check: health score penalty (-10 points for sat_fat > 5.0) must be preserved on cache hit
        assert res2["health_score"] == res1["health_score"]

        from services.food_health_score import compute_food_health_score
        from services.score_explainer import explain_score

        # Score with sat_fat=12.0 must be 10 points lower than with sat_fat=2.0
        score_low_sat = compute_food_health_score({**res2, "saturated_fat": 2.0})["health_score"]
        score_high_sat = compute_food_health_score(res2)["health_score"]
        assert score_low_sat - score_high_sat == 10

        explanation = explain_score(res2, None)
        sat_step = next(s for s in explanation["steps"] if s["factor"] == "Saturated fat")
        assert sat_step["impact"] == -10


# ==============================================================================
# 6. FSSAI CLAIM VERIFICATION FOR ZERO TRANS FAT
# ==============================================================================

def test_fssai_zero_trans_fat_claim_verification():
    """Verify FSSAI claim outcomes for supported, unsupported, and insufficient-data scenarios."""
    # Case A: Supported (trans fat < 0.2 and saturated fat <= 1.5)
    res_supported = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"trans_fat": 0.05, "saturated_fat": 1.2},
    )
    assert res_supported["status"] == "SUPPORTED"

    # Case B: Unsupported due to saturated fat limit (trans fat < 0.2 but saturated fat > 1.5)
    res_sat_exceeded = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"trans_fat": 0.05, "saturated_fat": 3.8},
    )
    assert res_sat_exceeded["status"] == "NOT_SUPPORTED"
    assert "saturated fat (3.8 g/100g) exceeds the allowable limit of 1.5 g/100g" in res_sat_exceeded["reason"]

    # Case C: Unsupported due to trans fat limit (trans fat >= 0.2)
    res_trans_exceeded = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"trans_fat": 0.35, "saturated_fat": 1.0},
    )
    assert res_trans_exceeded["status"] == "NOT_SUPPORTED"

    # Case D: Insufficient data when trans fat is missing
    res_insufficient = verify_single_claim(
        "Zero Trans Fat",
        nutrition={"saturated_fat": 1.0},
    )
    assert res_insufficient["status"] == "INSUFFICIENT_DATA"


# ==============================================================================
# 7. API BACKWARD COMPATIBILITY AND ENDPOINTS CONTRACT
# ==============================================================================

def test_api_contract_and_backward_compatibility(monkeypatch):
    """Verify FastAPI endpoints handle saturated and trans fats cleanly with backward compatibility."""
    client = TestClient(app)

    # 1. AnalyzeRequest with both fats
    req_payload = {
        "product_name": "Premium Granola",
        "calories": 400.0,
        "fat": 15.0,
        "saturated_fat": 2.5,
        "trans_fat": 0.02,
        "sugar": 10.0,
        "salt": 0.2,
        "protein": 10.0,
        "fiber": 8.0,
        "carbs": 55.0,
        "serving_size": 50.0,
    }
    # Mock authentication
    from services.auth_service import create_access_token
    token = create_access_token(user_id=1)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/analyze", json=req_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["product"]["nutrition"]["saturated_fat"] == 2.5
    assert data["product"]["nutrition"]["trans_fat"] == 0.02
    assert data["product"]["nutrition_per_serving"]["saturated_fat"] == 1.25
    assert data["product"]["nutrition_per_serving"]["trans_fat"] == 0.01

    # 2. AnalyzeRequest without trans_fat (backward compatibility)
    legacy_payload = {
        "product_name": "Legacy Granola",
        "calories": 400.0,
        "fat": 15.0,
        "sugar": 10.0,
    }
    legacy_resp = client.post("/analyze", json=legacy_payload, headers=headers)
    assert legacy_resp.status_code == 200
    legacy_data = legacy_resp.json()
    assert legacy_data["product"]["nutrition"]["saturated_fat"] is None
    assert legacy_data["product"]["nutrition"]["trans_fat"] is None

    # 3. Food log creation and update with fats
    log_payload = {
        "product_name": "Morning Oatmeal",
        "barcode": "manual",
        "calories": 250.0,
        "fat": 4.0,
        "saturated_fat": 0.8,
        "trans_fat": 0.0,
        "sugar": 3.0,
    }
    log_resp = client.post("/food-log", json=log_payload, headers=headers)
    assert log_resp.status_code == 200
    assert log_resp.json()["status"] == "logged"
