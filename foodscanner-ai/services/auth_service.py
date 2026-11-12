from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from services.config import get_secret_key, load_environment
from database.db_session import get_db
from database.models import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt", "argon2"], deprecated="auto")
security = HTTPBearer(auto_error=False)

load_environment()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def _get_secret_key() -> str:
    try:
        return get_secret_key()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


def _get_access_token_expire_minutes() -> int:
    raw = os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 60


def _get_refresh_token_expire_days() -> int:
    raw = os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "30").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


class TokenRevocationStore:
    """Thread-safe revocation blocklist with memory-bounded TTL eviction and optional Redis sync."""

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._revoked: Dict[str, float] = {}
        self._redis_client = redis_client
        self._redis_attempted_init = False

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def set_redis_client(self, client: Optional[Any]) -> None:
        with self._lock:
            self._redis_client = client
            self._redis_attempted_init = True

    def _get_or_init_redis(self) -> Optional[Any]:
        if self._redis_client is not None:
            return self._redis_client

        if self._redis_attempted_init:
            return None

        with self._lock:
            if self._redis_attempted_init:
                return self._redis_client
            self._redis_attempted_init = True

            try:
                from services.rate_limiter import get_rate_limiter
                limiter = get_rate_limiter()
                if limiter.is_redis_active() and limiter._redis_client is not None:
                    self._redis_client = limiter._redis_client
                    return self._redis_client
            except Exception:
                pass

            redis_url = os.getenv("REDIS_URL")
            if not redis_url or not redis_url.strip():
                return None

            try:
                import redis as redis_lib
                client = redis_lib.from_url(
                    redis_url.strip(),
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=1.0,
                )
                client.ping()
                self._redis_client = client
                logger.info("TokenRevocationStore connected to Redis distributed backend.")
                return self._redis_client
            except Exception as exc:
                logger.warning("TokenRevocationStore failed to initialize Redis: %s", exc)
                return None

    def revoke_token(self, token: str) -> bool:
        """Mark a token as revoked until its expiration."""
        if not token:
            return False
        clean_token = token.strip()
        now = time.time()
        exp_ts = now + 86400.0  # fallback 24h
        jti = None
        try:
            claims = jwt.get_unverified_claims(clean_token)
            if isinstance(claims, dict):
                exp_ts = float(claims.get("exp", exp_ts))
                jti = claims.get("jti")
        except Exception:
            pass

        token_hash = self._hash_token(clean_token)
        ttl = max(1, int(exp_ts - now))

        with self._lock:
            self._prune_expired_locked(now)
            self._revoked[token_hash] = exp_ts
            if jti:
                self._revoked[f"jti:{jti}"] = exp_ts

        client = self._get_or_init_redis()
        if client is not None:
            try:
                client.setex(f"revoked:{token_hash}", ttl, "1")
                if jti:
                    client.setex(f"revoked:jti:{jti}", ttl, "1")
            except Exception as exc:
                logger.warning("Failed to record token revocation in Redis: %s", exc)

        return True

    def claim_and_revoke_refresh_token(self, token: str) -> bool:
        """Atomically claim and revoke a refresh token during rotation.

        Returns:
            True if the token was successfully claimed and marked revoked.
            False if the token was ALREADY claimed or revoked (replay/reuse detected).
        """
        if not token:
            return False
        clean_token = token.strip()
        now = time.time()
        exp_ts = now + 86400.0 * 30  # default 30 days
        jti = None
        try:
            claims = jwt.get_unverified_claims(clean_token)
            if isinstance(claims, dict):
                exp_ts = float(claims.get("exp", exp_ts))
                jti = claims.get("jti")
        except Exception:
            pass

        token_hash = self._hash_token(clean_token)
        ttl = max(1, int(exp_ts - now))

        client = self._get_or_init_redis()
        if client is not None:
            try:
                # Atomic SET NX in Redis: returns True only if key did not exist
                acquired = client.set(f"revoked:{token_hash}", "1", nx=True, ex=ttl)
                if not acquired:
                    logger.warning("Refresh token replay detected via Redis: %s", token_hash[:16])
                    return False
                if jti:
                    client.set(f"revoked:jti:{jti}", "1", nx=True, ex=ttl)
                with self._lock:
                    self._revoked[token_hash] = exp_ts
                return True
            except Exception as exc:
                logger.warning("Redis atomic refresh claim failed (%s); checking local state", exc)

        with self._lock:
            self._prune_expired_locked(now)
            if token_hash in self._revoked and self._revoked[token_hash] > now:
                logger.warning("Refresh token replay detected via local store: %s", token_hash[:16])
                return False
            if jti and f"jti:{jti}" in self._revoked and self._revoked[f"jti:{jti}"] > now:
                logger.warning("Refresh token JTI replay detected via local store: %s", jti[:16])
                return False
            self._revoked[token_hash] = exp_ts
            if jti:
                self._revoked[f"jti:{jti}"] = exp_ts
            return True

    def is_revoked(self, token: str) -> bool:
        """Check whether token or its JTI is in the revocation store."""
        if not token:
            return False
        clean_token = token.strip()
        token_hash = self._hash_token(clean_token)
        now = time.time()

        with self._lock:
            if token_hash in self._revoked:
                if self._revoked[token_hash] > now:
                    return True
                else:
                    self._revoked.pop(token_hash, None)

        try:
            claims = jwt.get_unverified_claims(clean_token)
            if isinstance(claims, dict) and "jti" in claims:
                jti_key = f"jti:{claims['jti']}"
                with self._lock:
                    if jti_key in self._revoked:
                        if self._revoked[jti_key] > now:
                            return True
                        else:
                            self._revoked.pop(jti_key, None)
        except Exception:
            pass

        client = self._get_or_init_redis()
        if client is not None:
            try:
                if client.exists(f"revoked:{token_hash}"):
                    return True
            except Exception as exc:
                logger.debug("Redis token revocation check exception: %s", exc)

        return False

    def _prune_expired_locked(self, now: float) -> None:
        expired = [k for k, exp in self._revoked.items() if exp <= now]
        for k in expired:
            self._revoked.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._revoked.clear()
            self._redis_client = None
            self._redis_attempted_init = False


_REVOCATION_STORE = TokenRevocationStore()


def get_revocation_store() -> TokenRevocationStore:
    return _REVOCATION_STORE


def reset_revocation_store() -> None:
    _REVOCATION_STORE.reset()


def revoke_token(token: str) -> bool:
    return _REVOCATION_STORE.revoke_token(token)


def claim_and_revoke_refresh_token(token: str) -> bool:
    return _REVOCATION_STORE.claim_and_revoke_refresh_token(token)


def is_token_revoked(token: str) -> bool:
    return _REVOCATION_STORE.is_revoked(token)


def create_access_token(*, user_id: int, expires_delta: timedelta | None = None) -> str:
    minutes = _get_access_token_expire_minutes()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=minutes))
    to_encode = {
        "sub": str(user_id),
        "type": "access",
        "jti": uuid.uuid4().hex,
        "exp": expire,
    }
    return jwt.encode(to_encode, _get_secret_key(), algorithm="HS256")


def create_refresh_token(*, user_id: int, expires_delta: timedelta | None = None) -> str:
    days = _get_refresh_token_expire_days()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=days))
    to_encode = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "exp": expire,
    }
    return jwt.encode(to_encode, _get_secret_key(), algorithm="HS256")


def verify_access_token(token: str) -> int:
    if is_token_revoked(token):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise ValueError("missing sub")
        token_type = payload.get("type")
        if token_type == "refresh":
            raise HTTPException(status_code=401, detail="Cannot use refresh token as access token")
        return int(sub)
    except HTTPException:
        raise
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def verify_refresh_token(token: str) -> int:
    if is_token_revoked(token):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise ValueError("missing sub")
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type: expected refresh token")
        return int(sub)
    except HTTPException:
        raise
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization token")

    user_id = verify_access_token(credentials.credentials)
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
