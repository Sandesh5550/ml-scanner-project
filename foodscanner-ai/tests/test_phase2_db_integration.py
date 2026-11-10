from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alembic.config import Config

from database.canonical_adapter import (
    apply_canonical_to_db_product,
    canonical_to_db_models,
    db_product_to_canonical,
)
from database.models import Base, Nutrition, Product, ProductBarcodeAlias
from database.orm import create_database_engine, get_engine_options, resolve_database_url
from schemas.product import (
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    Nutrition100g,
)
from services.db_service import (
    add_barcode_alias,
    create_nutrition,
    create_product,
    get_canonical_product_by_barcode,
    get_product_by_barcode,
)

BASE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BASE_DIR / "alembic.ini"


def _get_alembic_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


class TestExistingDataPreservation:
    """Verifies that existing product and nutrition data survive migration 004 intact."""

    def test_existing_data_preserved_and_nulls_remain_null(self, tmp_path):
        db_path = tmp_path / "preserve_test.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)

        # 1. Establish baseline at 003
        command.upgrade(cfg, "003_add_saturated_and_trans_fat")
        engine = create_database_engine(db_url)

        # 2. Insert realistic historical records (including nulls)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO products (id, barcode, product_name, brand, nutriscore, ingredients, additives, created_at)
                    VALUES 
                    (1, '8901058851304', 'Maggi 2-Minute Noodles', 'Nestle', 'd', 'Wheat flour, Palm oil, Salt', 'INS 508', '2026-01-01 00:00:00'),
                    (2, '8901030000000', 'Partial Snack Item', 'LocalBrand', NULL, 'Rice flour, salt', NULL, '2026-01-02 00:00:00'),
                    (3, '8901099999999', 'Zero Nutrition Item', NULL, NULL, NULL, NULL, '2026-01-03 00:00:00')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO nutrition (id, product_id, calories, fat, saturated_fat, trans_fat, sugar, salt, protein, fiber, carbs)
                    VALUES 
                    (1, 1, 427.0, 15.7, 6.8, 0.1, 2.2, 2.3, 8.0, 3.5, 63.5),
                    (2, 2, 200.0, 5.0, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
                    """
                )
            )

        # 3. Apply migration 004
        command.upgrade(cfg, "004_add_provenance_and_aliases")

        # 4. Verify existing records survive with exact values
        with engine.connect() as conn:
            p1 = conn.execute(text("SELECT barcode, product_name, brand, nutriscore, ingredients, additives, canonical_id, data_source, data_confidence FROM products WHERE id = 1")).fetchone()
            assert p1[0] == "8901058851304"
            assert p1[1] == "Maggi 2-Minute Noodles"
            assert p1[2] == "Nestle"
            assert p1[3] == "d"
            assert p1[4] == "Wheat flour, Palm oil, Salt"
            assert p1[5] == "INS 508"
            assert p1[6] == "8901058851304"  # Backfilled from barcode
            assert p1[7] == "local_db"        # Backfilled default
            assert p1[8] == "UNVERIFIED"      # Backfilled default

            # Verify nutrition row 1
            n1 = conn.execute(text("SELECT calories, fat, saturated_fat, trans_fat, sugar, salt, protein, fiber, carbs, sodium FROM nutrition WHERE product_id = 1")).fetchone()
            assert n1[0] == 427.0
            assert n1[1] == 15.7
            assert n1[2] == 6.8
            assert n1[3] == 0.1
            assert n1[4] == 2.2
            assert n1[5] == 2.3
            assert n1[6] == 8.0
            assert n1[7] == 3.5
            assert n1[8] == 63.5
            assert n1[9] is None  # New column remains NULL for legacy rows

            # Verify product 2 and its NULL nutrition fields
            p2 = conn.execute(text("SELECT brand, nutriscore, additives, canonical_id FROM products WHERE id = 2")).fetchone()
            assert p2[0] == "LocalBrand"
            assert p2[1] is None  # Preserved NULL
            assert p2[2] is None  # Preserved NULL
            assert p2[3] == "8901030000000"

            n2 = conn.execute(text("SELECT calories, fat, saturated_fat, sugar, salt FROM nutrition WHERE product_id = 2")).fetchone()
            assert n2[0] == 200.0
            assert n2[1] == 5.0
            assert n2[2] is None  # Preserved NULL, NEVER converted to 0.0
            assert n2[3] is None  # Preserved NULL
            assert n2[4] is None  # Preserved NULL

            # Verify product 3 has no nutrition row and NULLs preserved
            p3 = conn.execute(text("SELECT product_name, brand, canonical_id FROM products WHERE id = 3")).fetchone()
            assert p3[0] == "Zero Nutrition Item"
            assert p3[1] is None
            assert p3[2] == "8901099999999"

            # Verify historical completeness remains NULL and is NEVER backfilled with misleading 0.0
            for pid in (1, 2, 3):
                comp = conn.execute(text(f"SELECT nutrition_completeness, ingredients_completeness FROM products WHERE id = {pid}")).fetchone()
                assert comp[0] is None, f"Expected product {pid} nutrition_completeness to be NULL, got {comp[0]}"
                assert comp[1] is None, f"Expected product {pid} ingredients_completeness to be NULL, got {comp[1]}"


class TestCanonicalMapping:
    """Verifies conversion between database entities and CanonicalProduct contract."""

    def test_complete_database_product_maps_correctly(self):
        prod = Product(
            id=10,
            barcode="8901058851304",
            canonical_id="UPC-8901058851304",
            product_name="Maggi 2-Minute Masala Noodles",
            brand="Nestle",
            category="Instant Noodles",
            nutriscore="d",
            ingredients="Wheat flour, Palm oil, Salt, Spices",
            additives="INS 508",
            data_source="local_db",
            data_confidence="UNVERIFIED",
            created_at="2026-01-01 00:00:00",
        )
        nutr = Nutrition(
            id=10,
            product_id=10,
            calories=427.0,
            fat=15.7,
            saturated_fat=6.8,
            trans_fat=0.1,
            carbs=63.5,
            sugar=2.2,
            protein=8.0,
            fiber=3.5,
            salt=2.3,
            sodium=None,
        )
        prod.nutrition = nutr

        canonical = db_product_to_canonical(prod)

        assert canonical.canonical_id == "UPC-8901058851304"
        assert canonical.barcode == "8901058851304"
        assert canonical.product_name == "Maggi 2-Minute Masala Noodles"
        assert canonical.brand == "Nestle"
        assert canonical.category == "Instant Noodles"
        assert canonical.nutrition.calories == 427.0
        assert canonical.nutrition.sugar == 2.2
        assert canonical.nutrition.salt == 2.3
        assert canonical.nutrition.sodium is None
        # Phase 1 derived properties
        assert canonical.assessment_status == AssessmentStatus.ASSESSABLE
        assert canonical.overall_confidence == DataConfidence.HIGH
        assert canonical.nutrition_completeness == 1.0
        assert canonical.ingredients_completeness == 1.0  # 0.7 ingr + 0.3 additives

    def test_partially_populated_product_maps_correctly(self):
        prod = Product(
            id=20,
            barcode="PARTIAL-001",
            product_name="Partial Snack",
            created_at="2026-01-01 00:00:00",
        )
        nutr = Nutrition(
            id=20,
            product_id=20,
            calories=250.0,
            fat=10.0,
            sugar=None,
            salt=None,
        )
        prod.nutrition = nutr

        canonical = db_product_to_canonical(prod)

        assert canonical.barcode == "PARTIAL-001"
        assert canonical.nutrition.calories == 250.0
        assert canonical.nutrition.fat == 10.0
        assert canonical.nutrition.sugar is None
        assert canonical.nutrition.salt is None
        assert canonical.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert canonical.overall_confidence == DataConfidence.LOW
        assert canonical.nutrition_completeness > 0.0
        assert canonical.nutrition_completeness < 1.0

    def test_completely_missing_nutrition_remains_unassessable(self):
        prod = Product(
            id=30,
            barcode="EMPTY-001",
            product_name="Empty Item",
            created_at="2026-01-01 00:00:00",
        )
        prod.nutrition = None

        canonical = db_product_to_canonical(prod)

        assert canonical.nutrition.calories is None
        assert canonical.nutrition.fat is None
        assert canonical.assessment_status == AssessmentStatus.UNASSESSABLE
        assert canonical.overall_confidence == DataConfidence.UNVERIFIED
        assert canonical.nutrition_completeness == 0.0

    def test_sodium_only_data_satisfies_phase1_rules(self):
        prod = Product(
            id=40,
            barcode="SODIUM-001",
            product_name="Sodium Item",
            ingredients="Wheat, water",
            created_at="2026-01-01 00:00:00",
        )
        # Declares calories, fat, sugar, and sodium (salt is None)
        nutr = Nutrition(
            id=40,
            product_id=40,
            calories=200.0,
            fat=5.0,
            sugar=3.0,
            salt=None,
            sodium=0.2,  # 0.2g sodium * 2.5 = 0.5g salt
        )
        prod.nutrition = nutr

        canonical = db_product_to_canonical(prod)

        assert canonical.nutrition.salt is None
        assert canonical.nutrition.sodium == 0.2
        # Salt slot is satisfied via resolve_effective_salt
        assert canonical.assessment_status == AssessmentStatus.ASSESSABLE
        assert canonical.overall_confidence == DataConfidence.HIGH

    def test_historical_provenance_is_never_falsely_marked_high(self):
        prod = Product(
            id=50,
            barcode="HIST-001",
            product_name="Historical Item",
            data_source="local_db",
            data_confidence="UNVERIFIED",
            created_at="2026-01-01 00:00:00",
        )
        # Empty nutrition
        prod.nutrition = Nutrition(id=50, product_id=50)

        canonical = db_product_to_canonical(prod)

        # Confidence must derive as UNVERIFIED, never falsely elevated
        assert canonical.overall_confidence == DataConfidence.UNVERIFIED
        assert canonical.assessment_status == AssessmentStatus.UNASSESSABLE


class TestDatabaseBehaviorAndAliases:
    """Verifies relational behavior, foreign keys, and alternate barcode aliases."""

    def test_barcode_alias_lookup_and_resolution(self, tmp_path):
        db_path = tmp_path / "alias_test.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            # 1. Create product
            prod = create_product(
                db,
                {
                    "barcode": "8901058851304",
                    "canonical_id": "PRD-MAGGI-001",
                    "product_name": "Maggi Noodles Single Pack",
                    "brand": "Nestle",
                },
            )
            create_nutrition(
                db,
                {
                    "product_id": prod.id,
                    "calories": 427.0,
                    "fat": 15.7,
                    "sugar": 2.2,
                    "salt": 2.3,
                },
            )
            db.commit()

            # 2. Add multipack and case barcode aliases
            add_barcode_alias(db, prod.id, "8901058851311", alias_type="multipack_4x")
            add_barcode_alias(db, prod.id, "8901058851328", alias_type="case_24x")
            db.commit()

            # 3. Lookup by primary barcode
            res_primary = get_product_by_barcode(db, "8901058851304")
            assert res_primary is not None
            assert res_primary["id"] == prod.id
            assert res_primary["product_name"] == "Maggi Noodles Single Pack"

            # 4. Lookup by alias barcode
            res_alias = get_product_by_barcode(db, "8901058851311")
            assert res_alias is not None
            assert res_alias["id"] == prod.id
            assert res_alias["product_name"] == "Maggi Noodles Single Pack"

            # 5. Canonical lookup by alias barcode includes alternate_barcodes list
            canon = get_canonical_product_by_barcode(db, "8901058851328")
            assert canon is not None
            assert canon.barcode == "8901058851304"
            assert canon.canonical_id == "PRD-MAGGI-001"
            assert "8901058851311" in canon.alternate_barcodes
            assert "8901058851328" in canon.alternate_barcodes

    def test_duplicate_alias_barcode_rejected_by_unique_constraint(self, tmp_path):
        db_path = tmp_path / "unique_test.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            p1 = create_product(db, {"barcode": "11111111", "product_name": "Product 1"})
            p2 = create_product(db, {"barcode": "22222222", "product_name": "Product 2"})
            db.commit()

            # Assign alias to p1
            add_barcode_alias(db, p1.id, "ALIAS-SHARED")
            db.commit()

            # Attempting to assign same alias to p2 directly via ORM must raise IntegrityError
            alias_conflict = ProductBarcodeAlias(
                product_id=p2.id,
                alias_barcode="ALIAS-SHARED",
                created_at="2026-01-01",
            )
            db.add(alias_conflict)
            with pytest.raises(IntegrityError):
                db.commit()

    def test_cascade_delete_clears_aliases_and_nutrition(self, tmp_path):
        from sqlalchemy import event
        import sqlite3

        db_path = tmp_path / "cascade_test.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)

        # Enforce foreign keys on SQLite for runtime CASCADE deletion verification
        @event.listens_for(engine, "connect")
        def _set_sqlite_fk(dbapi_conn, conn_record):
            if isinstance(dbapi_conn, sqlite3.Connection):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys = ON;")
                cur.close()

        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            prod = create_product(db, {"barcode": "DEL-001", "product_name": "To Delete"})
            create_nutrition(db, {"product_id": prod.id, "calories": 100.0})
            add_barcode_alias(db, prod.id, "DEL-ALIAS-001")
            db.commit()

            prod_id = prod.id

            # Delete product
            db.delete(prod)
            db.commit()

            # Verify nutrition and alias are deleted via CASCADE
            nutr_cnt = db.execute(select(Nutrition).where(Nutrition.product_id == prod_id)).all()
            alias_cnt = db.execute(select(ProductBarcodeAlias).where(ProductBarcodeAlias.product_id == prod_id)).all()
            assert len(nutr_cnt) == 0
            assert len(alias_cnt) == 0

    def test_self_alias_creation_is_rejected(self, tmp_path):
        db_path = tmp_path / "self_alias.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            prod = create_product(db, {"barcode": "SELF-001", "product_name": "Self Alias Test"})
            db.commit()

            with pytest.raises(ValueError, match="alias cannot be identical to target product's primary barcode"):
                add_barcode_alias(db, prod.id, "SELF-001")

    def test_alias_colliding_with_primary_barcode_is_rejected(self, tmp_path):
        db_path = tmp_path / "collision_alias.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            p1 = create_product(db, {"barcode": "PRIMARY-A", "product_name": "Product A"})
            p2 = create_product(db, {"barcode": "PRIMARY-B", "product_name": "Product B"})
            db.commit()

            # Attempting to assign Product A's primary barcode as an alias for Product B must be rejected
            with pytest.raises(ValueError, match="already exists as primary barcode for product id"):
                add_barcode_alias(db, p2.id, "PRIMARY-A")

    def test_duplicate_alias_for_same_product_is_idempotent(self, tmp_path):
        db_path = tmp_path / "idempotent_alias.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            p = create_product(db, {"barcode": "IDEM-001", "product_name": "Idempotent Product"})
            db.commit()

            a1 = add_barcode_alias(db, p.id, "ALIAS-IDEM-X")
            db.commit()
            a2 = add_barcode_alias(db, p.id, "ALIAS-IDEM-X")
            assert a1.id == a2.id
            assert a1.alias_barcode == a2.alias_barcode

    def test_alias_already_owned_by_another_product_raises_value_error(self, tmp_path):
        db_path = tmp_path / "owned_alias.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            p1 = create_product(db, {"barcode": "OWNED-A", "product_name": "Owner A"})
            p2 = create_product(db, {"barcode": "OWNED-B", "product_name": "Owner B"})
            db.commit()

            add_barcode_alias(db, p1.id, "ALIAS-EXCLUSIVE")
            db.commit()

            with pytest.raises(ValueError, match="already belongs to product id"):
                add_barcode_alias(db, p2.id, "ALIAS-EXCLUSIVE")

    def test_alias_for_nonexistent_product_raises_value_error(self, tmp_path):
        db_path = tmp_path / "nonexistent_prod.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            with pytest.raises(ValueError, match="target product with id 999999 does not exist"):
                add_barcode_alias(db, 999999, "ALIAS-ORPHAN")


class TestDataIntegrityAndCompleteness:
    """Verifies that completeness columns are not polluted with misleading 0.0 values."""

    def test_new_product_completeness_defaults_to_null_not_misleading_zero(self, tmp_path):
        db_path = tmp_path / "null_comp.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            # Create product without providing completeness values
            prod = create_product(db, {"barcode": "COMP-001", "product_name": "Uncalculated Item"})
            db.commit()

            # Python ORM model attributes should be None
            assert prod.nutrition_completeness is None
            assert prod.ingredients_completeness is None

            # Raw SQL check: underlying DB columns must be NULL, never 0.0
            row = db.execute(
                text("SELECT nutrition_completeness, ingredients_completeness FROM products WHERE barcode = 'COMP-001'")
            ).fetchone()
            assert row[0] is None, f"Expected DB nutrition_completeness to be NULL, got {row[0]}"
            assert row[1] is None, f"Expected DB ingredients_completeness to be NULL, got {row[1]}"

    def test_canonical_adapter_computes_completeness_when_db_cache_is_null(self, tmp_path):
        db_path = tmp_path / "adapter_null_comp.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_database_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)

        with SessionLocal() as db:
            prod = create_product(
                db,
                {
                    "barcode": "ADAPT-001",
                    "product_name": "Rich Product With Null DB Cache",
                    "ingredients": "Wheat flour, Sugar, Palm oil",
                },
            )
            create_nutrition(
                db,
                {
                    "product_id": prod.id,
                    "calories": 300.0,
                    "fat": 10.0,
                    "saturated_fat": 2.0,
                    "carbs": 40.0,
                    "sugar": 5.0,
                    "protein": 8.0,
                    "fiber": 3.0,
                    "salt": 1.2,
                },
            )
            db.commit()

            # Verify DB cache is strictly NULL
            assert prod.nutrition_completeness is None
            assert prod.ingredients_completeness is None

            # Adapter maps to CanonicalProduct: completeness must be calculated dynamically, NOT 0.0 or None
            canonical = db_product_to_canonical(prod)
            assert canonical.nutrition_completeness == 1.0  # 4 of 4 mandatory nutrients disclosed
            assert canonical.ingredients_completeness == 0.7  # ingredients present, additives absent
            assert canonical.assessment_status == AssessmentStatus.ASSESSABLE

            # When explicitly applying canonical back to DB model, values are persisted
            apply_canonical_to_db_product(canonical, prod)
            db.commit()

            assert prod.nutrition_completeness == 1.0
            assert prod.ingredients_completeness == 0.7


class TestMigrationUpgradeDowngrade:
    """Verifies Alembic 004 upgrade, downgrade, and re-upgrade lifecycle."""

    def test_full_upgrade_and_downgrade_cycle(self, tmp_path):
        db_path = tmp_path / "lifecycle.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        cfg = _get_alembic_config(db_url)

        # 1. Upgrade to head (004)
        command.upgrade(cfg, "head")
        engine = create_database_engine(db_url)

        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "004_add_provenance_and_aliases"

        inspector = inspect(engine)
        assert "product_barcode_aliases" in set(inspector.get_table_names())
        prod_cols = {c["name"] for c in inspector.get_columns("products")}
        assert "canonical_id" in prod_cols
        assert "data_source" in prod_cols
        assert "sodium" in {c["name"] for c in inspector.get_columns("nutrition")}

        # 2. Downgrade to 003
        command.downgrade(cfg, "003_add_saturated_and_trans_fat")

        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "003_add_saturated_and_trans_fat"

        inspector_down = inspect(engine)
        assert "product_barcode_aliases" not in set(inspector_down.get_table_names())
        prod_cols_down = {c["name"] for c in inspector_down.get_columns("products")}
        assert "canonical_id" not in prod_cols_down
        assert "sodium" not in {c["name"] for c in inspector_down.get_columns("nutrition")}

        # 3. Re-upgrade to head (004)
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "004_add_provenance_and_aliases"


class TestPostgreSQLDialectCompatibility:
    """Verifies that models and DDL compile cleanly under the PostgreSQL dialect."""

    def test_postgresql_ddl_compilation(self):
        from sqlalchemy.schema import CreateTable
        from sqlalchemy.dialects import postgresql

        pg_dialect = postgresql.dialect()

        # Compile CreateTable for each model under PostgreSQL dialect
        ddl_product = str(CreateTable(Product.__table__).compile(dialect=pg_dialect))
        assert "products" in ddl_product
        assert "canonical_id VARCHAR(64)" in ddl_product or "canonical_id VARCHAR" in ddl_product
        assert "data_source VARCHAR(50)" in ddl_product or "data_source VARCHAR" in ddl_product

        ddl_nutrition = str(CreateTable(Nutrition.__table__).compile(dialect=pg_dialect))
        assert "nutrition" in ddl_nutrition
        assert "sodium FLOAT" in ddl_nutrition or "sodium DOUBLE PRECISION" in ddl_nutrition

        ddl_aliases = str(CreateTable(ProductBarcodeAlias.__table__).compile(dialect=pg_dialect))
        assert "product_barcode_aliases" in ddl_aliases
        assert "alias_barcode VARCHAR(64)" in ddl_aliases or "alias_barcode VARCHAR" in ddl_aliases
        assert "REFERENCES products (id)" in ddl_aliases or "REFERENCES products" in ddl_aliases

    def test_postgresql_connection_options(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_SIZE", "20")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "40")
        monkeypatch.setenv("DB_POOL_TIMEOUT", "60")
        monkeypatch.setenv("DB_POOL_RECYCLE", "1200")

        pg_url = "postgresql://user:pass@ep-cool-db.us-east-2.aws.neon.tech/neondb?sslmode=require"
        normalized = resolve_database_url(pg_url)
        assert normalized.startswith("postgresql+psycopg2://")

        opts = get_engine_options(normalized)
        assert opts["pool_size"] == 20
        assert opts["max_overflow"] == 40
        assert opts["pool_timeout"] == 60
        assert opts["pool_recycle"] == 1200
        assert opts["pool_pre_ping"] is True
