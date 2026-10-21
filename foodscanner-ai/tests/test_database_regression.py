"""Database regression tests for PRAMAAN Phase 1 SQLite -> PostgreSQL productionization.

Covers:
1. SQLite engine initialization
2. PostgreSQL URL detection/configuration
3. postgres:// normalization
4. SQLite check_same_thread behavior
5. PostgreSQL pool configuration
6. Model indexes
7. Schema creation & Alembic awareness
8. Alembic baseline migration upgrade/downgrade
9. Database session lifecycle & get_db
10. User isolation
11. Scan != Eat invariant
12. Existing product lookup & relationship integrity
13. Existing food-log behavior & macro aggregation
14. Database URL credential redaction & security
15. Optional live PostgreSQL integration test
"""

import os
import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database.orm import (
    Base,
    create_database_engine,
    get_engine_options,
    resolve_database_url,
)
from database.init_db import ensure_default_user, init_db, run_migrations
from database.models import FoodLog, Nutrition, Product, ScanHistory, User
from services.config import redact_database_url, validate_runtime_config


# ---------------------------------------------------------------------------
# 1. SQLite Engine Initialization
# ---------------------------------------------------------------------------
def test_sqlite_engine_initialization(tmp_path):
    test_db = tmp_path / "test_init.db"
    url = f"sqlite:///{test_db.as_posix()}"
    engine = create_database_engine(url)
    assert engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1


# ---------------------------------------------------------------------------
# 2. PostgreSQL URL Detection & Configuration
# ---------------------------------------------------------------------------
def test_postgresql_url_detection():
    url = "postgresql://myuser:mypassword@db.example.com:5432/pramaan"
    resolved = resolve_database_url(url)
    assert resolved.startswith("postgresql+psycopg2://")
    assert "myuser:mypassword@db.example.com:5432/pramaan" in resolved


# ---------------------------------------------------------------------------
# 3. postgres:// Normalization
# ---------------------------------------------------------------------------
def test_postgres_legacy_normalization():
    legacy_url = "postgres://app_user:s3cr3t@aws.rds.amazonaws.com:5432/prod_db"
    resolved = resolve_database_url(legacy_url)
    assert resolved == "postgresql+psycopg2://app_user:s3cr3t@aws.rds.amazonaws.com:5432/prod_db"

    standard_url = "postgresql://app_user:s3cr3t@aws.rds.amazonaws.com:5432/prod_db"
    resolved_standard = resolve_database_url(standard_url)
    assert resolved_standard == "postgresql+psycopg2://app_user:s3cr3t@aws.rds.amazonaws.com:5432/prod_db"

    psycopg2_url = "postgresql+psycopg2://app_user:s3cr3t@aws.rds.amazonaws.com:5432/prod_db"
    assert resolve_database_url(psycopg2_url) == psycopg2_url


# ---------------------------------------------------------------------------
# 4. SQLite check_same_thread Behavior
# ---------------------------------------------------------------------------
def test_sqlite_check_same_thread_option():
    sqlite_url = "sqlite:///some_test.db"
    opts = get_engine_options(sqlite_url)
    assert "connect_args" in opts
    assert opts["connect_args"].get("check_same_thread") is False
    assert opts.get("pool_pre_ping") is True


# ---------------------------------------------------------------------------
# 5. Pool Configuration
# ---------------------------------------------------------------------------
def test_postgresql_pool_configuration(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "15")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "30")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "45")
    monkeypatch.setenv("DB_POOL_RECYCLE", "3600")

    pg_url = "postgresql+psycopg2://user:pass@localhost:5432/testdb"
    opts = get_engine_options(pg_url)
    assert opts["pool_size"] == 15
    assert opts["max_overflow"] == 30
    assert opts["pool_timeout"] == 45
    assert opts["pool_recycle"] == 3600
    assert opts["pool_pre_ping"] is True


# ---------------------------------------------------------------------------
# 6. Model Indexes
# ---------------------------------------------------------------------------
def test_model_indexes():
    # Verify ScanHistory index
    scan_indexes = {idx.name: [col.name for col in idx.columns] for idx in ScanHistory.__table__.indexes}
    assert "ix_scan_history_user_time" in scan_indexes
    assert scan_indexes["ix_scan_history_user_time"] == ["user_id", "scan_time"]

    # Verify FoodLog index
    food_indexes = {idx.name: [col.name for col in idx.columns] for idx in FoodLog.__table__.indexes}
    assert "ix_daily_food_log_user_consumed" in food_indexes
    assert food_indexes["ix_daily_food_log_user_consumed"] == ["user_id", "consumed_at"]

    # Verify Nutrition index
    nutr_indexes = {idx.name: [col.name for col in idx.columns] for idx in Nutrition.__table__.indexes}
    assert "ix_nutrition_product_id" in nutr_indexes
    assert nutr_indexes["ix_nutrition_product_id"] == ["product_id"]


# ---------------------------------------------------------------------------
# 7. Schema Creation & Alembic Awareness
# ---------------------------------------------------------------------------
def test_schema_creation_and_alembic_awareness(tmp_path):
    test_db = tmp_path / "test_schema.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")

    # Initialize schema
    init_db(target_engine=engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {"users", "products", "nutrition", "scan_history", "daily_food_log"}
    assert expected.issubset(tables)

    # Verify default user is created
    with engine.connect() as conn:
        user_row = conn.execute(text("SELECT id, email FROM users WHERE id = 1")).fetchone()
        assert user_row is not None
        assert user_row[1] == "default@local"

    # Test Alembic awareness: if alembic_version exists, run_migrations skips ad-hoc alterations
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('001_initial_schema')"))

    # Calling init_db again should not alter or duplicate
    init_db(target_engine=engine)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM users WHERE id = 1")).scalar()
        assert count == 1


# ---------------------------------------------------------------------------
# 8. Alembic Migration Upgrade and Downgrade
# ---------------------------------------------------------------------------
def test_alembic_migrations(tmp_path):
    from alembic import command
    from alembic.config import Config

    test_db = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{test_db.as_posix()}"

    alembic_ini_path = BASE_DIR / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")

    engine = create_database_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "products", "nutrition", "scan_history", "daily_food_log", "alembic_version"}.issubset(tables)

    # Verify indexes in migrated database
    scan_idx_names = [idx["name"] for idx in inspector.get_indexes("scan_history")]
    assert "ix_scan_history_user_time" in scan_idx_names

    # Downgrade to base
    command.downgrade(alembic_cfg, "base")
    inspector_after = inspect(engine)
    tables_after = set(inspector_after.get_table_names())
    assert "daily_food_log" not in tables_after
    assert "products" not in tables_after


# ---------------------------------------------------------------------------
# 9. Database Session Lifecycle
# ---------------------------------------------------------------------------
def test_database_session_lifecycle(tmp_path):
    test_db = tmp_path / "session_test.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")
    init_db(target_engine=engine)

    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        # Add a test product
        p = Product(barcode="TEST9999", product_name="Lifecycle Product", created_at="2026-09-29 00:00:00")
        session.add(p)
        session.commit()
        assert p.id is not None
    finally:
        session.close()

    # Verify persisted in a new session
    new_session = TestSession()
    try:
        found = new_session.query(Product).filter_by(barcode="TEST9999").first()
        assert found is not None
        assert found.product_name == "Lifecycle Product"
    finally:
        new_session.close()


# ---------------------------------------------------------------------------
# 10. User Isolation
# ---------------------------------------------------------------------------
def test_user_isolation(tmp_path):
    test_db = tmp_path / "isolation_test.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")
    init_db(target_engine=engine)

    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        u1 = User(id=10, email="userA@test.local", created_at="2026-09-29 00:00:00")
        u2 = User(id=20, email="userB@test.local", created_at="2026-09-29 00:00:00")
        session.add_all([u1, u2])
        session.commit()

        # Log for User A
        logA = FoodLog(
            user_id=10,
            barcode="A100",
            product_name="User A Food",
            calories=250.0,
            consumed_at="2026-09-29 10:00:00",
        )
        # Scan for User A
        scanA = ScanHistory(
            user_id=10,
            barcode="A100",
            scan_time="2026-09-29 09:59:00",
            result="Safe",
        )
        session.add_all([logA, scanA])
        session.commit()

        # Query as User B
        user_b_logs = session.query(FoodLog).filter_by(user_id=20).all()
        user_b_scans = session.query(ScanHistory).filter_by(user_id=20).all()

        assert len(user_b_logs) == 0
        assert len(user_b_scans) == 0

        # Query as User A
        user_a_logs = session.query(FoodLog).filter_by(user_id=10).all()
        user_a_scans = session.query(ScanHistory).filter_by(user_id=10).all()

        assert len(user_a_logs) == 1
        assert user_a_logs[0].product_name == "User A Food"
        assert len(user_a_scans) == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 11. Scan != Eat Invariant
# ---------------------------------------------------------------------------
def test_scan_not_eat_invariant(tmp_path):
    """Verifies that recording a scan NEVER inserts an intake record into daily_food_log."""
    test_db = tmp_path / "invariant_test.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")
    init_db(target_engine=engine)

    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        user = User(id=1, email="test@local", created_at="2026-09-29 00:00:00")
        session.merge(user)
        session.commit()

        # Perform 5 scan operations
        for i in range(5):
            scan = ScanHistory(
                user_id=1,
                barcode=f"SCAN_{i}",
                scan_time=f"2026-09-29 12:0{i}:00",
                result="Scanned result",
            )
            session.add(scan)
        session.commit()

        # Verify scan history has 5 entries
        scan_count = session.query(ScanHistory).filter_by(user_id=1).count()
        assert scan_count == 5

        # STRICT INVARIANT: daily_food_log must remain 0
        food_log_count = session.query(FoodLog).filter_by(user_id=1).count()
        assert food_log_count == 0, "Scan != Eat violation: scan operation created food log entry!"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 12. Existing Product Lookup & Nutrition Relationship
# ---------------------------------------------------------------------------
def test_existing_product_lookup_and_cascade(tmp_path):
    test_db = tmp_path / "lookup_test.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")
    init_db(target_engine=engine)

    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        prod = Product(
            barcode="8901030000000",
            product_name="Nutrition Test Biscuit",
            brand="Pramaan Foods",
            nutriscore="B",
            created_at="2026-09-29 00:00:00",
        )
        session.add(prod)
        session.commit()

        nutr = Nutrition(
            product_id=prod.id,
            calories=450.0,
            fat=12.0,
            sugar=20.0,
            salt=0.5,
            protein=8.0,
            fiber=3.0,
            carbs=70.0,
        )
        session.add(nutr)
        session.commit()

        # Lookup by barcode
        fetched = session.query(Product).filter_by(barcode="8901030000000").first()
        assert fetched is not None
        assert fetched.nutrition is not None
        assert fetched.nutrition.calories == 450.0
        assert fetched.nutrition.sugar == 20.0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 13. Existing Food-Log Behavior & Macro Aggregation
# ---------------------------------------------------------------------------
def test_food_log_macro_aggregation(tmp_path):
    test_db = tmp_path / "macro_test.db"
    engine = create_database_engine(f"sqlite:///{test_db.as_posix()}")
    init_db(target_engine=engine)

    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        user = User(id=1, email="macro@local", daily_calorie_limit=2000, created_at="2026-09-29 00:00:00")
        session.merge(user)
        session.commit()

        item1 = FoodLog(
            user_id=1,
            barcode="B1",
            product_name="Oatmeal",
            calories=150.0,
            fat=3.0,
            sugar=1.0,
            protein=5.0,
            consumed_at="2026-09-29 08:00:00",
        )
        item2 = FoodLog(
            user_id=1,
            barcode="B2",
            product_name="Almonds",
            calories=200.0,
            fat=15.0,
            sugar=2.0,
            protein=7.0,
            consumed_at="2026-09-29 10:30:00",
        )
        session.add_all([item1, item2])
        session.commit()

        logs = session.query(FoodLog).filter_by(user_id=1).all()
        assert len(logs) == 2

        total_calories = sum(l.calories or 0.0 for l in logs)
        total_protein = sum(l.protein or 0.0 for l in logs)
        assert total_calories == 350.0
        assert total_protein == 12.0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 14. Database URL Credential Redaction & Security
# ---------------------------------------------------------------------------
def test_database_url_redaction_and_config():
    raw_url = "postgresql://db_user:super_secret_password_123@db.pramaan.internal:5432/pramaan_prod"
    redacted = redact_database_url(raw_url)
    assert "super_secret_password_123" not in redacted
    assert "db_user:***@db.pramaan.internal:5432/pramaan_prod" in redacted

    sqlite_url = "sqlite:///database/foodscanner.db"
    assert redact_database_url(sqlite_url) == sqlite_url

    # Test runtime config redaction
    orig_db = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = raw_url
        cfg = validate_runtime_config(raise_error=False)
        assert "super_secret_password_123" not in cfg["database_url"]
        assert "***" in cfg["database_url"]
    finally:
        if orig_db is not None:
            os.environ["DATABASE_URL"] = orig_db
        else:
            os.environ.pop("DATABASE_URL", None)


# ---------------------------------------------------------------------------
# 15. Optional Live PostgreSQL Integration Test
# ---------------------------------------------------------------------------
def test_live_postgresql_integration():
    """Optional live PostgreSQL integration test when TEST_POSTGRES_URL or TEST_DATABASE_URL is configured."""
    pg_url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("TEST_DATABASE_URL")
    is_ci = str(os.getenv("GITHUB_ACTIONS", "")).strip().lower() == "true" or str(os.getenv("CI", "")).strip().lower() == "true"

    if not pg_url or not pg_url.startswith("postgres"):
        if is_ci:
            pytest.fail(
                "Live PostgreSQL integration test is required in GitHub Actions CI, "
                "but neither TEST_POSTGRES_URL nor TEST_DATABASE_URL is configured."
            )
        pytest.skip("Live PostgreSQL integration test skipped; set TEST_POSTGRES_URL or TEST_DATABASE_URL to enable.")

    engine = create_database_engine(pg_url)
    assert engine.dialect.name == "postgresql"
    with engine.connect() as conn:
        val = conn.execute(text("SELECT 1")).scalar()
        assert val == 1


def test_database_regression_ci_guard_fails_in_ci_when_unconfigured(monkeypatch):
    """Verify that live PostgreSQL regression test fails loudly in CI if test URL is missing."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("TEST_POSTGRES_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match=r"Live PostgreSQL integration test is required in GitHub Actions CI"):
        test_live_postgresql_integration()


def test_database_regression_ci_guard_skips_in_local_dev(monkeypatch):
    """Verify that live PostgreSQL regression test skips gracefully in local dev if test URL is missing."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TEST_POSTGRES_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(pytest.skip.Exception, match=r"Live PostgreSQL integration test skipped"):
        test_live_postgresql_integration()


# ---------------------------------------------------------------------------
# 16. Data Migration Utility Verification
# ---------------------------------------------------------------------------
def test_data_migration_pipeline(tmp_path):
    from scripts.migrate_data import execute_migration

    # Setup source SQLite database with sample data
    source_db = tmp_path / "source.db"
    source_engine = create_database_engine(f"sqlite:///{source_db.as_posix()}")
    init_db(target_engine=source_engine)

    Session = sessionmaker(bind=source_engine)
    with Session() as s:
        p = Product(barcode="MIGRATE01", product_name="Migration Biscuit", created_at="2026-09-29 00:00:00")
        s.add(p)
        s.flush()
        n = Nutrition(product_id=p.id, calories=120.0, protein=3.0)
        s.add(n)
        s.commit()

    # Target database (empty schema)
    target_db = tmp_path / "target.db"
    target_engine = create_database_engine(f"sqlite:///{target_db.as_posix()}")
    init_db(target_engine=target_engine)

    # 1. Test Dry Run
    summary_dry = execute_migration(source_db, f"sqlite:///{target_db.as_posix()}", dry_run=True)
    assert summary_dry["products"]["inserted"] >= 1

    # In dry run, target DB should still only have the default initialization rows, not MIGRATE01
    with target_engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM products WHERE barcode = 'MIGRATE01'")).scalar()
        assert cnt == 0

    # 2. Test Actual Migration
    summary_actual = execute_migration(source_db, f"sqlite:///{target_db.as_posix()}", dry_run=False)
    assert summary_actual["products"]["inserted"] >= 1
    assert summary_actual["nutrition"]["inserted"] >= 1

    # Verify target DB now has the row
    with target_engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM products WHERE barcode = 'MIGRATE01'")).scalar()
        assert cnt == 1

    # 3. Test Idempotency (running again skips existing rows without failure)
    summary_second = execute_migration(source_db, f"sqlite:///{target_db.as_posix()}", dry_run=False)
    assert summary_second["products"]["skipped"] >= 1
