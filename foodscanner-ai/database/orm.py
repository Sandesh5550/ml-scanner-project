from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DB_DIR = Path(__file__).resolve().parent
DEFAULT_DB_FILE = DB_DIR / "foodscanner.db"

# Load .env from current working directory and/or foodscanner-ai directory
load_dotenv()
env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)


def resolve_database_url(url: Optional[str] = None) -> str:
    raw = (url or os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        return f"sqlite:///{DEFAULT_DB_FILE.as_posix()}"

    # Normalize legacy postgres:// (standard in Render, Supabase, Heroku) to postgresql+psycopg2://
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg2://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://") and not raw.startswith("postgresql+"):
        raw = "postgresql+psycopg2://" + raw[len("postgresql://"):]

    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:///:memory:"):
        path_str = raw[len("sqlite:///"):]
        p = Path(path_str)
        if not p.is_absolute():
            cwd_path = Path.cwd() / p
            if cwd_path.parent.exists() and (cwd_path.exists() or Path.cwd() == BASE_DIR):
                return f"sqlite:///{cwd_path.resolve().as_posix()}"
            resolved = (BASE_DIR / p).resolve()
            if resolved.parent.exists():
                return f"sqlite:///{resolved.as_posix()}"
            return f"sqlite:///{DEFAULT_DB_FILE.as_posix()}"
    return raw


def get_engine_options(url: str) -> Dict[str, Any]:
    """Build production-safe SQLAlchemy engine options depending on dialect."""
    opts: Dict[str, Any] = {"pool_pre_ping": True}
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        opts["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL production connection pooling configuration
        try:
            pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
        except (ValueError, TypeError):
            pool_size = 10

        try:
            max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        except (ValueError, TypeError):
            max_overflow = 20

        try:
            pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        except (ValueError, TypeError):
            pool_timeout = 30

        try:
            pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
        except (ValueError, TypeError):
            pool_recycle = 1800

        opts["pool_size"] = pool_size
        opts["max_overflow"] = max_overflow
        opts["pool_timeout"] = pool_timeout
        opts["pool_recycle"] = pool_recycle

    return opts


def create_database_engine(url: Optional[str] = None) -> Engine:
    db_url = resolve_database_url(url)
    options = get_engine_options(db_url)
    return create_engine(db_url, **options)


DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_FILE.as_posix()}"
DATABASE_URL = resolve_database_url()

engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db(target_engine: Optional[Engine] = None, auto_upgrade: bool = False) -> None:
    """Initialize database tables and run schema migrations.

    Delegates to database.init_db.init_db which respects Alembic-managed production
    schemas and dialect-safe initialization.
    """
    from database.init_db import init_db as _init_db_impl

    _init_db_impl(target_engine=target_engine or engine, auto_upgrade=auto_upgrade)

