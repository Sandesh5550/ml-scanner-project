from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Base paths
AI_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = AI_DIR.parent


def redact_database_url(url: Optional[str]) -> str:
    """Redact password and sensitive credentials from a database URL.
    
    Example:
        postgresql://user:secret123@host:5432/dbname -> postgresql://user:***@host:5432/dbname
        sqlite:///database/foodscanner.db -> sqlite:///database/foodscanner.db
    """
    if not url:
        return ""
    # Regex matching: ://[user[:password]@]host
    pattern = re.compile(r"(:\/\/[^:]+):([^@]+)(@)")
    return pattern.sub(r"\1:***\3", url)


def find_env_file() -> Optional[Path]:
    """Locate .env file across standard project locations."""
    candidates = [
        AI_DIR / ".env",
        ROOT_DIR / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "foodscanner-ai" / ".env",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def load_environment(dotenv_path: Optional[Path | str] = None, override: bool = False) -> Optional[Path]:
    """Load environment variables from .env file without overriding existing env vars."""
    target_path = Path(dotenv_path) if dotenv_path else find_env_file()
    if target_path and target_path.exists():
        load_dotenv(dotenv_path=target_path, override=override)
        logger.info("Loaded environment from: %s", target_path)
        return target_path
    return None


INSECURE_DEV_KEYS = {
    "pramaan-local-dev-secret-key-minimum-32-chars",
    "dev-insecure-secret-key",
    "changeme",
    "secret",
    "password",
    "12345678",
}


def get_secret_key() -> str:
    """Retrieve the configured SECRET_KEY.
    
    Raises RuntimeError if SECRET_KEY is not configured or if insecure auth
    or placeholder keys are improperly attempted in a production environment.
    """
    secret = os.environ.get("SECRET_KEY")
    env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    is_prod = env in {"production", "prod"}

    if not secret:
        # Check if insecure dev auth is explicitly opted into
        allow_insecure = str(os.environ.get("ALLOW_INSECURE_DEV_AUTH") or "").strip().lower() in {"1", "true", "yes"}
        if allow_insecure:
            if is_prod:
                raise RuntimeError("ALLOW_INSECURE_DEV_AUTH is strictly forbidden in production environments.")
            return "dev-insecure-secret-key"
        raise RuntimeError(
            "SECRET_KEY is not configured. Create foodscanner-ai/.env from .env.example and set SECRET_KEY."
        )

    if is_prod:
        if secret.strip().lower() in INSECURE_DEV_KEYS:
            raise RuntimeError("SECRET_KEY cannot use an insecure development placeholder in production.")
        if len(secret.strip()) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters long for production security.")

    return secret


def validate_runtime_config(raise_error: bool = True) -> Dict[str, Any]:
    """Validate critical runtime configuration at startup.
    
    Ensures SECRET_KEY is set and environment is ready.
    In production environments (ENVIRONMENT=production/prod):
      - Rejects ALLOW_INSECURE_DEV_AUTH
      - Rejects default development placeholder keys
      - Requires SECRET_KEY to have at least 32 characters
    Returns a dictionary of validated configuration with redacted secrets.
    """
    # Attempt to load .env if SECRET_KEY is not yet in environment
    if not os.environ.get("SECRET_KEY"):
        load_environment()

    errors = []
    secret = os.environ.get("SECRET_KEY")
    allow_insecure = str(os.environ.get("ALLOW_INSECURE_DEV_AUTH") or "").strip().lower() in {"1", "true", "yes"}
    env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    is_prod = env in {"production", "prod"}

    if is_prod and allow_insecure:
        errors.append("ALLOW_INSECURE_DEV_AUTH is strictly forbidden in production environments.")

    if not secret and not allow_insecure:
        errors.append(
            "SECRET_KEY is not configured. Create foodscanner-ai/.env from .env.example and set SECRET_KEY."
        )

    # Production-specific cryptographic key validation
    if is_prod and secret:
        if secret.strip().lower() in INSECURE_DEV_KEYS:
            errors.append("SECRET_KEY cannot use an insecure development placeholder in production.")
        elif len(secret.strip()) < 32:
            errors.append("SECRET_KEY must be at least 32 characters long for production security.")

    raw_db_url = os.environ.get("DATABASE_URL")
    if raw_db_url:
        safe_db_url = redact_database_url(raw_db_url)
    else:
        safe_db_url = "sqlite:///database/foodscanner.db (default)"

    if errors and raise_error:
        error_msg = "\n".join(errors)
        logger.critical("Runtime configuration failure:\n%s (DB: %s)", error_msg, safe_db_url)
        raise RuntimeError(error_msg)

    ai_provider = os.environ.get("AI_PROVIDER") or "mock (default)"
    ai_model = os.environ.get("AI_MODEL") or "gemini-1.5-flash (default)"

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "environment": env or "development",
        "secret_key_set": bool(secret or allow_insecure),
        "database_url": safe_db_url,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
    }
