from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "foodscanner.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def run_migrations(engine: Engine) -> None:
    """Run lightweight schema migrations safely for dev/test environments.
    
    If Alembic has already versioned the database (alembic_version exists),
    this function yields authority to Alembic and performs no alterations.
    """
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())

            # Yield to Alembic if running on an Alembic-versioned database
            if "alembic_version" in table_names:
                logger.info("Database is versioned by Alembic; skipping ad-hoc migrations.")
                return

            # Users table migrations
            if "users" in table_names:
                cols = {c["name"] for c in inspector.get_columns("users")}
                if "name" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN name VARCHAR(255)"))
                if "hashed_password" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN hashed_password VARCHAR(255) DEFAULT ''"))
                if "age" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN age INTEGER"))
                if "weight" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN weight FLOAT"))
                if "height" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN height FLOAT"))
                if "goal_type" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN goal_type VARCHAR(100)"))
                if "goal_target_days" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN goal_target_days INTEGER DEFAULT 30"))
                if "goal_started_at" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN goal_started_at VARCHAR(100)"))
                if "allergens" not in cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN allergens VARCHAR(255)"))


            # Products table migrations
            if "products" in table_names:
                p_cols = {c["name"] for c in inspector.get_columns("products")}
                if "brand" not in p_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN brand VARCHAR(255)"))
                if "ingredients" not in p_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN ingredients TEXT"))
                if "additives" not in p_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN additives TEXT"))

            # Scan history migrations
            if "scan_history" in table_names:
                s_cols = {c["name"] for c in inspector.get_columns("scan_history")}
                if "user_id" not in s_cols:
                    conn.execute(text("ALTER TABLE scan_history ADD COLUMN user_id INTEGER DEFAULT 1"))

            # Daily food log migrations
            if "daily_food_log" in table_names:
                log_cols = {c["name"] for c in inspector.get_columns("daily_food_log")}
                if "user_id" not in log_cols:
                    conn.execute(text("ALTER TABLE daily_food_log ADD COLUMN user_id INTEGER DEFAULT 1"))
                for macro_col in ("fat", "sugar", "salt", "protein", "fiber", "carbs"):
                    if macro_col not in log_cols:
                        conn.execute(text(f"ALTER TABLE daily_food_log ADD COLUMN {macro_col} FLOAT"))
    except Exception as e:
        logger.error(f"Migration check failed: {e}")
        raise


import os


def should_create_default_user() -> bool:
    """Determine whether to create default user with empty password.

    Production environments (ENVIRONMENT=production/prod or SKIP_DEFAULT_USER=true)
    strictly forbid creating default unhashed credentials.
    """
    env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    if env in {"production", "prod"}:
        return False
    skip = str(os.getenv("SKIP_DEFAULT_USER", "")).strip().lower() in {"1", "true", "yes"}
    return not skip


def ensure_default_user(bind: Union[Engine, Connection]) -> None:
    """Ensure a default user with ID 1 exists safely across SQLite and PostgreSQL in dev/test.

    In production environments (ENVIRONMENT=production or SKIP_DEFAULT_USER=true),
    default user creation is bypassed to prevent unhashed default credentials.
    """
    if not should_create_default_user():
        logger.info("Production environment detected or SKIP_DEFAULT_USER=true; skipping default user creation.")
        return

    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _ensure_default_user_conn(conn)
    else:
        _ensure_default_user_conn(bind)


def _ensure_default_user_conn(conn: Connection) -> None:
    inspector = inspect(conn)
    table_names = set(inspector.get_table_names())
    if "users" not in table_names:
        return

    row = conn.execute(text("SELECT id FROM users WHERE id = :user_id"), {"user_id": 1}).fetchone()
    if row is None:
        email_row = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": "default@local"}).fetchone()
        if email_row is None:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, name, email, hashed_password, daily_calorie_limit, diet_type, created_at)
                    VALUES (:id, :name, :email, :hashed_password, :daily_calorie_limit, :diet_type, :created_at)
                    """
                ),
                {
                    "id": 1,
                    "name": "Default User",
                    "email": "default@local",
                    "hashed_password": "",
                    "daily_calorie_limit": 2000,
                    "diet_type": None,
                    "created_at": now_str,
                },
            )
            # For PostgreSQL, ensure sequence matches max(id)
            if conn.dialect.name == "postgresql":
                try:
                    conn.execute(text("SELECT setval(pg_get_serial_sequence('users', 'id'), coalesce(max(id), 1), true) FROM users;"))
                except Exception:
                    pass


def init_db(
    db_path: Optional[Path] = None,
    schema_path: Optional[Path] = None,
    target_engine: Optional[Engine] = None,
) -> None:
    """Dialect-safe database initialization for both SQLite and PostgreSQL.
    
    Uses declarative SQLAlchemy schema creation rather than raw SQLite scripts.
    In production environments managed by Alembic, this does not overwrite migration schema.
    """
    from database.orm import Base, create_database_engine, engine as default_engine

    if target_engine is not None:
        eng = target_engine
    elif db_path is not None:
        # Caller specified a custom path (e.g. SQLite path)
        eng = create_database_engine(f"sqlite:///{Path(db_path).resolve().as_posix()}")
    else:
        eng = default_engine

    # Import models so Base.metadata has all table definitions
    from database import models  # noqa: F401

    with eng.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        is_alembic_managed = "alembic_version" in table_names

        # If not managed by Alembic, create tables via SQLAlchemy Base metadata
        if not is_alembic_managed:
            Base.metadata.create_all(bind=conn)

    # Run any column additions if needed for non-alembic dev environments
    run_migrations(eng)
    ensure_default_user(eng)
    logger.info(f"Database initialized safely for dialect: {eng.dialect.name}")


if __name__ == "__main__":
    init_db()
