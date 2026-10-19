from __future__ import annotations

from collections import defaultdict, deque
import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """Thread-safe, process-local sliding-window rate limiter for FastAPI.

    NOTE: This implementation is process-local. In multi-worker or multi-replica
    environments, each process maintains its own independent window. Distributed
    rate limiting across replicas (e.g. via Redis) is planned for a future phase.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[Tuple[str, str], deque] = defaultdict(deque)

    def is_enabled(self) -> bool:
        """Check if rate limiting is enabled via environment."""
        val = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
        return val in {"1", "true", "yes"}

    def get_limits(self) -> Dict[str, Tuple[int, int]]:
        """Retrieve configured limits: {endpoint_key: (max_requests, window_seconds)}."""
        ocr_limit = int(os.getenv("RATE_LIMIT_OCR", "5"))
        chat_limit = int(os.getenv("RATE_LIMIT_CHAT", "15"))
        scan_limit = int(os.getenv("RATE_LIMIT_SCAN", "30"))
        auth_limit = int(os.getenv("RATE_LIMIT_AUTH", "5"))
        window = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        return {
            "ocr": (ocr_limit, window),
            "chat": (chat_limit, window),
            "scan": (scan_limit, window),
            "auth": (auth_limit, window),
        }

    def check_rate_limit(
        self,
        endpoint_key: str,
        client_key: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, int]:
        """Check and record an access attempt.

        Returns:
            (is_allowed, retry_after_seconds)
        """
        if not self.is_enabled():
            return True, 0

        limits = self.get_limits()
        if endpoint_key not in limits:
            return True, 0

        max_requests, window = limits[endpoint_key]
        now = current_time if current_time is not None else time.time()
        window_start = now - window

        with self._lock:
            q = self._records[(endpoint_key, client_key)]

            # Purge timestamps outside the active sliding window
            while q and q[0] <= window_start:
                q.popleft()

            if len(q) >= max_requests:
                earliest = q[0]
                retry_after = max(1, int(window - (now - earliest)))
                return False, retry_after

            q.append(now)
            return True, 0

    def reset(self) -> None:
        """Reset internal rate limit tracking records. Used for test isolation."""
        with self._lock:
            self._records.clear()


# Global process-local rate limiter instance
_global_rate_limiter = SlidingWindowRateLimiter()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Retrieve the singleton rate limiter instance."""
    return _global_rate_limiter


def get_client_key(request: Request, user_id: Optional[int] = None) -> str:
    """Determine client identifier: authenticated user ID or client IP."""
    if user_id is not None:
        return f"user:{user_id}"

    # Fallback to client IP for unauthenticated requests
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "true").strip().lower() in {"1", "true", "yes"}
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
            if client_ip and len(client_ip) <= 64 and not any(c in client_ip for c in " \t\r\n"):
                return f"ip:{client_ip}"

    if request.client and request.client.host:
        return f"ip:{request.client.host}"

    return "ip:unknown"


def enforce_rate_limit(
    endpoint_key: str,
    request: Request,
    user_id: Optional[int] = None,
    limiter: Optional[SlidingWindowRateLimiter] = None,
) -> None:
    """Validate client request against configured rate limits.

    Raises:
        HTTPException(429) with Retry-After header if limit is exceeded.
    """
    active_limiter = limiter or get_rate_limiter()
    client_key = get_client_key(request, user_id=user_id)

    allowed, retry_after = active_limiter.check_rate_limit(endpoint_key, client_key)
    if not allowed:
        logger.warning(
            "Rate limit exceeded for endpoint '%s' by client '%s'. Retry after %ds.",
            endpoint_key,
            client_key,
            retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {endpoint_key}. Please try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
