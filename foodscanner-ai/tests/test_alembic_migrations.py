from __future__ import annotations

import os
from pathlib import Path
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config

from database.init_db import init_db, run_migrations
from database.models import Base, OCRJob, User
from database.orm import create_database_engine
from api.main import recover_stale_ocr_jobs

BASE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BASE_DIR / "alembic.ini"


def _get_alembic_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_fresh_sqlite_database_upgraded_to_head(tmp_path):
    """1. Verify fresh SQLite database upgraded directly to head."""
    test_db = tmp_path / "fresh_head.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Run upgrade to head
    command.upgrade(cfg, "head")

    engine = create_database_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected_tables = {
        "users",
        "products",
        "nutrition",
        "scan_history",
        "daily_food_log",
        "ocr_jobs",
        "alembic_version",
    }
    assert expected_tables.issubset(tables)

    # Verify users.allergens exists
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    assert "allergens" in user_cols

    # Verify ocr_jobs columns
    ocr_cols = {c["name"] for c in inspector.get_columns("ocr_jobs")}
    expected_ocr_cols = {
        "id",
        "user_id",
        "status",
        "image_hash",
        "result_json",
        "error_message",
        "created_at",
        "started_at",
        "completed_at",
    }
    assert expected_ocr_cols.issubset(ocr_cols)

    # Verify alembic_version revision ID
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "002_add_ocr_jobs_and_allergens"


def test_upgrade_from_001_preserves_user_data(tmp_path):
    """2. Verify upgrade from 001 preserves existing user data and adds allergens & ocr_jobs."""
    test_db = tmp_path / "upgrade_001.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Step A: Upgrade to 001 baseline
    command.upgrade(cfg, "001_initial_schema")

    engine = create_database_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "ocr_jobs" not in tables
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    assert "allergens" not in user_cols

    # Step B: Insert pre-existing user data
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO users (id, name, email, hashed_password, age, weight, height, daily_calorie_limit, diet_type, created_at)
                VALUES (1, 'Preserved User', 'user@preserve.com', 'secret_hash', 30, 72.5, 178.0, 2400, 'keto', '2026-10-20 00:00:00')
                """
            )
        )

    # Step C: Upgrade to head (002)
    command.upgrade(cfg, "head")

    # Step D: Verify user data was strictly preserved
    inspector_after = inspect(engine)
    assert "ocr_jobs" in set(inspector_after.get_table_names())
    assert "allergens" in {c["name"] for c in inspector_after.get_columns("users")}

    with engine.connect() as conn:
        row = conn.execute(text("SELECT name, email, age, weight, daily_calorie_limit, allergens FROM users WHERE id = 1")).fetchone()
        assert row is not None
        assert row[0] == "Preserved User"
        assert row[1] == "user@preserve.com"
        assert row[2] == 30
        assert row[3] == 72.5
        assert row[4] == 2400
        assert row[5] is None  # Initialized as NULL without modifying existing row data

    # Step E: Verify updating allergens and creating an OCRJob linked to the user
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        user = session.query(User).filter(User.id == 1).first()
        assert user is not None
        user.allergens = "peanuts,dairy"
        session.commit()

        job = OCRJob(
            id="job_test_123",
            user_id=1,
            status="pending",
            created_at="2026-10-20 01:00:00",
        )
        session.add(job)
        session.commit()

        queried_job = session.query(OCRJob).filter(OCRJob.id == "job_test_123").first()
        assert queried_job is not None
        assert queried_job.user.email == "user@preserve.com"
    finally:
        session.close()


def test_preexisting_dev_schema_idempotency(tmp_path):
    """3. Verify idempotency when ocr_jobs and/or allergens already exist in an existing DB."""
    test_db = tmp_path / "idempotent_dev.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)
    engine = create_database_engine(db_url)

    # Simulate existing dev database created by Base.metadata.create_all()
    # which already has ocr_jobs and users.allergens
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)
        # Stamp it as 001_initial_schema to simulate a migration upgrade on an existing dev DB
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('001_initial_schema')"))

    inspector_before = inspect(engine)
    assert "ocr_jobs" in set(inspector_before.get_table_names())
    assert "allergens" in {c["name"] for c in inspector_before.get_columns("users")}

    # Upgrade to head: should detect existing objects without throwing 'table already exists'
    command.upgrade(cfg, "head")

    inspector_after = inspect(engine)
    assert "ocr_jobs" in set(inspector_after.get_table_names())
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "002_add_ocr_jobs_and_allergens"


def test_ocr_indexes_and_foreign_keys(tmp_path):
    """4. Verify OCR job indexes and foreign key constraints."""
    test_db = tmp_path / "indexes_fks.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    command.upgrade(cfg, "head")

    engine = create_database_engine(db_url)
    inspector = inspect(engine)

    # Check indexes on ocr_jobs
    indexes = {idx["name"]: idx["column_names"] for idx in inspector.get_indexes("ocr_jobs")}
    assert "ix_ocr_jobs_user_id" in indexes
    assert indexes["ix_ocr_jobs_user_id"] == ["user_id"]
    assert "ix_ocr_jobs_status" in indexes
    assert indexes["ix_ocr_jobs_status"] == ["status"]

    # Check foreign keys
    fks = inspector.get_foreign_keys("ocr_jobs")
    assert len(fks) >= 1
    user_fk = next((fk for fk in fks if fk["referred_table"] == "users"), None)
    assert user_fk is not None
    assert user_fk["constrained_columns"] == ["user_id"]
    assert user_fk["referred_columns"] == ["id"]


def test_application_startup_against_alembic_managed_db(tmp_path):
    """5. Verify init_db with auto_upgrade=True brings Alembic database to head."""
    test_db = tmp_path / "startup_alembic.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Initialize at 001
    command.upgrade(cfg, "001_initial_schema")

    engine = create_database_engine(db_url)
    inspector = inspect(engine)
    assert "ocr_jobs" not in set(inspector.get_table_names())

    # Call init_db with auto_upgrade=True: should apply pending migrations to head
    init_db(target_engine=engine, auto_upgrade=True)

    inspector_after = inspect(engine)
    assert "ocr_jobs" in set(inspector_after.get_table_names())
    assert "allergens" in {c["name"] for c in inspector_after.get_columns("users")}

    # Verify application startup sweep (recover_stale_ocr_jobs) runs without error
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        reaped = recover_stale_ocr_jobs(session)
        assert reaped == 0
    finally:
        session.close()


def test_multiworker_startup_skips_inprocess_upgrade(tmp_path):
    """Verify that default startup (auto_upgrade=False) does not run Alembic upgrade, preventing concurrency races."""
    test_db = tmp_path / "multiworker_alembic.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Initialize at 001
    command.upgrade(cfg, "001_initial_schema")
    engine = create_database_engine(db_url)

    # Startup with default auto_upgrade=False: skips in-process upgrade
    init_db(target_engine=engine, auto_upgrade=False)

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "001_initial_schema"  # Not mutated during multi-worker startup!

    # Separate deployment migration step: explicitly upgrades to head
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "002_add_ocr_jobs_and_allergens"


def test_unversioned_fallback_repairs_missing_indexes(tmp_path):
    """Verify unversioned fallback in init_db creates required ocr_jobs indexes if missing."""
    test_db = tmp_path / "fallback_indexes.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    engine = create_database_engine(db_url)

    # Create unversioned schema with ocr_jobs but NO indexes
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    email TEXT NOT NULL UNIQUE,
                    hashed_password TEXT NOT NULL DEFAULT '',
                    daily_calorie_limit INTEGER NOT NULL DEFAULT 2000,
                    created_at TEXT NOT NULL
                );
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE ocr_jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    image_hash VARCHAR(64),
                    result_json TEXT,
                    error_message TEXT,
                    created_at VARCHAR(100) NOT NULL,
                    started_at VARCHAR(100),
                    completed_at VARCHAR(100),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                """
            )
        )

    inspector_before = inspect(engine)
    existing_indexes_before = {idx["name"] for idx in inspector_before.get_indexes("ocr_jobs")}
    assert "ix_ocr_jobs_user_id" not in existing_indexes_before
    assert "ix_ocr_jobs_status" not in existing_indexes_before

    # Run fallback init_db
    init_db(target_engine=engine)

    inspector_after = inspect(engine)
    existing_indexes_after = {idx["name"] for idx in inspector_after.get_indexes("ocr_jobs")}
    assert "ix_ocr_jobs_user_id" in existing_indexes_after
    assert "ix_ocr_jobs_status" in existing_indexes_after


def test_repeated_fallback_initialization_is_idempotent(tmp_path):
    """Verify calling fallback init_db multiple times on an unversioned DB is safe and repeatable."""
    test_db = tmp_path / "repeated_fallback.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    engine = create_database_engine(db_url)

    # First call
    init_db(target_engine=engine)
    # Second call (must be safe and idempotent)
    init_db(target_engine=engine)
    # Third call
    init_db(target_engine=engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "products", "nutrition", "scan_history", "daily_food_log", "ocr_jobs"}.issubset(tables)
    assert "alembic_version" not in tables  # Never automatically stamps unversioned DB


def test_unversioned_database_unsafe_upgrade_blocked_by_preflight(tmp_path):
    """Verify CLI upgrade head is blocked by preflight on an unversioned DB with existing tables."""
    test_db = tmp_path / "unsafe_unversioned.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)
    engine = create_database_engine(db_url)

    # Populate unversioned database
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)

    # Attempting to directly run alembic upgrade head must fail with clear preflight instructions
    with pytest.raises(RuntimeError, match="ALEMBIC SAFETY PREFLIGHT ERROR"):
        command.upgrade(cfg, "head")

    # Following operator guidance: stamp 001 first, then upgrade head
    command.stamp(cfg, "001_initial_schema")
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "002_add_ocr_jobs_and_allergens"


def test_migration_002_rejects_incompatible_schema(tmp_path):
    """Verify migration 002 detects and rejects incompatible ocr_jobs tables without corrupting data."""
    test_db = tmp_path / "incompatible_schema.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)
    engine = create_database_engine(db_url)

    # Create base tables and corrupt ocr_jobs missing user_id
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )
        )
        # Malformed ocr_jobs table missing user_id column
        conn.execute(
            text(
                """
                CREATE TABLE ocr_jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    status VARCHAR(20) NOT NULL
                );
                """
            )
        )
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('001_initial_schema')"))

    # Attempting to upgrade to head must fail with clear schema validation error
    with pytest.raises(RuntimeError, match="MIGRATION 002 ERROR.*missing critical columns"):
        command.upgrade(cfg, "head")


def test_downgrade_and_reupgrade_cycle(tmp_path):
    """6. Verify downgrade from 002 to 001, then to base, and re-upgrade to head."""
    test_db = tmp_path / "downgrade_cycle.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Start at head
    command.upgrade(cfg, "head")

    engine = create_database_engine(db_url)
    assert "ocr_jobs" in set(inspect(engine).get_table_names())

    # Downgrade to 001
    command.downgrade(cfg, "001_initial_schema")
    inspector_001 = inspect(engine)
    assert "ocr_jobs" not in set(inspector_001.get_table_names())
    assert "allergens" not in {c["name"] for c in inspector_001.get_columns("users")}

    # Downgrade to base
    command.downgrade(cfg, "base")
    inspector_base = inspect(engine)
    app_tables = set(inspector_base.get_table_names()) - {"alembic_version"}
    assert len(app_tables) == 0

    # Re-upgrade to head
    command.upgrade(cfg, "head")
    inspector_head = inspect(engine)
    assert "ocr_jobs" in set(inspector_head.get_table_names())
    assert "allergens" in {c["name"] for c in inspector_head.get_columns("users")}


def get_postgresql_test_url() -> str:
    """Retrieve configured PostgreSQL test connection URL.

    - In local dev (GITHUB_ACTIONS and CI unset): skips test gracefully if URL is unconfigured.
    - In GitHub Actions CI (GITHUB_ACTIONS=true or CI=true): fails immediately if URL is missing,
      preventing silent false-positive skips when CI is misconfigured.
    """
    pg_url = os.getenv("TEST_DATABASE_URL") or os.getenv("POSTGRES_TEST_URL")
    is_ci = str(os.getenv("GITHUB_ACTIONS", "")).strip().lower() == "true" or str(os.getenv("CI", "")).strip().lower() == "true"

    if not pg_url or not pg_url.startswith("postgres"):
        if is_ci:
            pytest.fail(
                "PostgreSQL integration test service is required in GitHub Actions CI, "
                "but neither TEST_DATABASE_URL nor POSTGRES_TEST_URL is configured."
            )
        pytest.skip("PostgreSQL test service not configured (TEST_DATABASE_URL not set).")

    return pg_url


def test_postgresql_compatibility_where_available():
    """7. Verify PostgreSQL compatibility when a live test database is configured."""
    pg_url = get_postgresql_test_url()

    cfg = _get_alembic_config(pg_url)
    engine = create_database_engine(pg_url)

    # Run upgrade to head
    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "products", "nutrition", "scan_history", "daily_food_log", "ocr_jobs", "alembic_version"}.issubset(tables)

    # Verify downgrade
    command.downgrade(cfg, "base")
    inspector_after = inspect(engine)
    assert "ocr_jobs" not in set(inspector_after.get_table_names())


def test_postgresql_ci_guard_fails_in_ci_when_unconfigured(monkeypatch):
    """Verify that when running in GitHub Actions CI, missing PostgreSQL test URL fails loudly."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_TEST_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match=r"PostgreSQL integration test service is required in GitHub Actions CI"):
        get_postgresql_test_url()


def test_postgresql_ci_guard_skips_in_local_dev_when_unconfigured(monkeypatch):
    """Verify that when running in local development, missing PostgreSQL test URL gracefully skips."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_TEST_URL", raising=False)

    with pytest.raises(pytest.skip.Exception, match=r"PostgreSQL test service not configured"):
        get_postgresql_test_url()


def test_render_configuration_has_pre_deploy_migration():
    """8. Verify render.yaml declares preDeployCommand for safe migration before workers start."""
    import yaml

    repo_root = BASE_DIR.parent
    render_yaml_path = repo_root / "render.yaml"
    assert render_yaml_path.exists(), f"render.yaml not found at {render_yaml_path}"

    with open(render_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    services = data.get("services", [])
    backend_service = next((s for s in services if s.get("name") == "foodscanner-ai"), None)
    assert backend_service is not None, "foodscanner-ai service not defined in render.yaml"

    # Pre-deploy migration command must be configured to run once before worker instances
    pre_deploy = backend_service.get("preDeployCommand")
    assert pre_deploy == "alembic upgrade head", f"Unexpected preDeployCommand: {pre_deploy}"

    # Verify DATABASE_URL is wired from foodscanner-db
    env_vars = {item["key"]: item for item in backend_service.get("envVars", [])}
    assert "DATABASE_URL" in env_vars
    assert env_vars["DATABASE_URL"].get("fromDatabase", {}).get("name") == "foodscanner-db"


def test_docker_compose_has_dedicated_one_shot_migration_service():
    """9. Verify docker-compose.yml defines a dedicated one-shot migration service."""
    import yaml

    repo_root = BASE_DIR.parent
    compose_path = repo_root / "docker-compose.yml"
    assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"

    with open(compose_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "migration" in services, "Dedicated 'migration' service missing from docker-compose.yml"
    assert "backend" in services, "'backend' service missing from docker-compose.yml"

    migration_svc = services["migration"]
    # Must not restart on completion
    assert migration_svc.get("restart") == "no"
    cmd = migration_svc.get("command")
    assert cmd == ["alembic", "upgrade", "head"] or "alembic upgrade head" in str(cmd)

    # Backend must wait for migration service to complete successfully
    backend_svc = services["backend"]
    depends_on = backend_svc.get("depends_on", {})
    assert "migration" in depends_on, "backend service does not depend on migration service"
    assert depends_on["migration"].get("condition") == "service_completed_successfully"

    # Scalability & recreation safety: neither migration nor backend should define
    # hardcoded container_name, preventing name conflicts when scaling replicas or recreating
    assert "container_name" not in migration_svc, "migration service should not have container_name for clean recreation"
    assert "container_name" not in backend_svc, "backend service should not have container_name to allow --scale backend=N"

    # Volume and database path parity
    assert "./foodscanner-ai/database:/app/database" in migration_svc.get("volumes", [])
    assert "./foodscanner-ai/database:/app/database" in backend_svc.get("volumes", [])


def test_migration_failure_aborts_cleanly(tmp_path):
    """10. Verify that a migration failure on an unversioned database raises an exception and aborts."""
    test_db = tmp_path / "failing_unversioned.db"
    db_url = f"sqlite:///{test_db.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Simulate an unversioned existing database with tables
    engine = create_database_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100))"))

    # Running upgrade head on this database must fail loudly and clearly
    with pytest.raises(RuntimeError, match=r"ALEMBIC SAFETY PREFLIGHT ERROR"):
        command.upgrade(cfg, "head")


def test_database_url_normalization_and_parity():
    """11. Verify DATABASE_URL normalization parity between Alembic and FastAPI application."""
    from database.orm import resolve_database_url

    # 1. Empty / None fallback
    default_resolved = resolve_database_url(None)
    assert default_resolved.startswith("sqlite:///")
    assert default_resolved.endswith("foodscanner.db")

    # 2. Render / Supabase legacy postgres:// normalization
    render_url = "postgres://user:pass@host:5432/dbname"
    normalized_render = resolve_database_url(render_url)
    assert normalized_render.startswith("postgresql+psycopg2://")
    assert "postgres:// " not in normalized_render

    # 3. Standard postgresql:// normalization
    standard_pg = "postgresql://user:pass@host.internal:5432/pramaan_db"
    normalized_pg = resolve_database_url(standard_pg)
    assert normalized_pg.startswith("postgresql+psycopg2://")

    # 4. Absolute container SQLite path preservation
    container_sqlite = "sqlite:////app/database/foodscanner.db"
    resolved_container = resolve_database_url(container_sqlite)
    assert resolved_container == "sqlite:////app/database/foodscanner.db"

    # 5. Relative SQLite path resolution
    rel_sqlite = "sqlite:///database/foodscanner.db"
    resolved_rel = resolve_database_url(rel_sqlite)
    assert resolved_rel.startswith("sqlite:///")
    assert Path(resolved_rel[len("sqlite:///"):]).is_absolute()
