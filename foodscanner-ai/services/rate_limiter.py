from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import ipaddress
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, Request

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

# Atomic Redis sliding-window Lua script.
# Returns {allowed (1 or 0), retry_after_seconds (0 or calculated)}
REDIS_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

-- Prefer authoritative Redis server TIME to eliminate inter-container clock skew/monotonic drift
local now = tonumber(ARGV[1])
local ok, rtime = pcall(redis.call, 'TIME')
if ok and rtime and #rtime >= 2 then
    now = tonumber(rtime[1]) + (tonumber(rtime[2]) / 1000000.0)
end

local clearBefore = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', clearBefore)
local currentCount = redis.call('ZCARD', key)
if currentCount < limit then
    redis.call('ZADD', key, now, now .. ':' .. math.random(100000, 999999))
    redis.call('EXPIRE', key, math.ceil(window) + 1)
    return {1, 0}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 1
    if oldest and #oldest >= 2 then
        local oldest_ts = tonumber(oldest[2])
        retry_after = math.max(1, math.ceil(window - (now - oldest_ts)))
    end
    return {0, retry_after}
end
"""


def _parse_int_env(
    key: str,
    default: int,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> int:
    """Safely parse an integer environment variable with fallback and bounds checking."""
    raw = os.getenv(key)
    if raw is None:
        return default
    raw_str = raw.strip()
    if not raw_str:
        return default
    try:
        val = int(raw_str)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid integer value for environment variable %s: '%s'. Using default %d.",
            key,
            raw_str[:32],
            default,
        )
        return default

    if min_val is not None and val < min_val:
        logger.warning(
            "Value for %s (%d) is below minimum allowed (%d). Clamping to %d.",
            key,
            val,
            min_val,
            min_val,
        )
        return min_val
    if max_val is not None and val > max_val:
        logger.warning(
            "Value for %s (%d) is above maximum allowed (%d). Clamping to %d.",
            key,
            val,
            max_val,
            max_val,
        )
        return max_val
    return val


def _parse_float_env(
    key: str,
    default: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Safely parse a float environment variable with fallback and bounds checking."""
    raw = os.getenv(key)
    if raw is None:
        return default
    raw_str = raw.strip()
    if not raw_str:
        return default
    try:
        val = float(raw_str)
        if math.isnan(val) or math.isinf(val):
            logger.warning(
                "Non-finite float value for environment variable %s: '%s'. Using default %s.",
                key,
                raw_str[:32],
                default,
            )
            return default
    except (ValueError, TypeError):
        logger.warning(
            "Invalid float value for environment variable %s: '%s'. Using default %s.",
            key,
            raw_str[:32],
            default,
        )
        return default

    if min_val is not None and val < min_val:
        logger.warning(
            "Value for %s (%s) is below minimum allowed (%s). Clamping to %s.",
            key,
            val,
            min_val,
            min_val,
        )
        return min_val
    if max_val is not None and val > max_val:
        logger.warning(
            "Value for %s (%s) is above maximum allowed (%s). Clamping to %s.",
            key,
            val,
            max_val,
            max_val,
        )
        return max_val
    return val


def _parse_trusted_networks(trusted_str: str) -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma-separated CIDR notations or IP addresses into ip_network objects."""
    networks: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in (trusted_str or "").split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        try:
            networks.append(ipaddress.ip_network(cleaned, strict=False))
        except ValueError:
            pass
    return networks


def extract_client_ip(request: Request) -> str:
    """Extract client IP address safely, taking trusted proxies into account.

    Proxy Architecture & Security Rules:
    1. Render Strategy (PROXY_PROVIDER == "render" or RENDER == "true"):
       Render routes all inbound traffic through Cloudflare edge. Cloudflare strips any
       client-supplied 'CF-Connecting-IP' header and sets it to the validated client
       IP address connecting to Cloudflare.
       - Primary: 'CF-Connecting-IP' validated with ipaddress.
       - Fallback: If CF-Connecting-IP is absent or invalid, do NOT trust X-Forwarded-For
         (which could contain client-spoofed hops); fall back safely to the direct
         socket peer (request.client.host) if valid, or 'unknown'.
       This strategy is enabled when PROXY_PROVIDER is 'render' or RENDER is 'true'
       (unless TRUSTED_PROXIES is configured or PROXY_PROVIDER is set to another value).
       TRUST_PROXY_HEADERS defaults to 'true' in Render mode unless explicitly overridden.

    2. Standard Local / Standalone Mode (Default):
       If TRUST_PROXY_HEADERS is false (default) and not in Render mode, all forwarded
       headers (CF-Connecting-IP, X-Forwarded-For, X-Real-IP) are completely ignored,
       and only the direct socket peer (request.client.host) is used.

    3. CIDR Mode (when TRUSTED_PROXIES is configured and TRUST_PROXY_HEADERS is true):
       The direct socket peer MUST be within one of the TRUSTED_PROXIES networks.
       If direct peer is untrusted, forwarded headers are completely ignored.
       When direct peer is verified, X-Forwarded-For is traversed from right to left;
       the rightmost IP that is NOT within TRUSTED_PROXIES is returned as client IP.
       If X-Forwarded-For contains no untrusted hop, X-Real-IP is accepted ONLY IF
       TRUST_REAL_IP_HEADER is explicitly true and direct peer is verified.

    4. Count Mode (TRUST_PROXY_HEADERS is true, TRUSTED_PROXIES is empty, PROXY_PROVIDER != "render"):
       Standard reverse proxies (e.g. Nginx $proxy_add_x_forwarded_for, AWS ALB)
       append the connecting peer IP to the end of the X-Forwarded-For header.
       Therefore, for N trusted proxy hops, the client IP is located at hops[-proxy_count].
       For TRUSTED_PROXY_COUNT=1 (single reverse proxy), the client IP is hops[-1].
       X-Real-IP is NEVER accepted in count mode without a verified peer IP.

    5. Every selected IP is validated using `ipaddress.ip_address`. If malformed or
       insufficient hops exist, safely falls back to direct socket peer IP (if valid),
       or 'unknown'.
    """
    direct_host = None
    if request.client and hasattr(request.client, "host") and isinstance(request.client.host, str):
        h = request.client.host.strip()
        if h:
            direct_host = h

    trusted_proxies_cfg = os.getenv("TRUSTED_PROXIES", "").strip()
    proxy_provider = os.getenv("PROXY_PROVIDER", "").strip().lower()
    is_render_env = os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes"}

    # Provider is Render if explicitly configured as 'render', or if running in Render environment
    # without explicit CIDR override or alternate provider.
    is_render = (proxy_provider == "render") or (
        is_render_env and not trusted_proxies_cfg and proxy_provider not in {"none", "generic"}
    )

    # Determine default for TRUST_PROXY_HEADERS: true on Render, false otherwise.
    default_trust_proxy = "true" if is_render else "false"
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", default_trust_proxy).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    if not trust_proxy:
        if direct_host:
            try:
                ipaddress.ip_address(direct_host)
                return direct_host
            except ValueError:
                pass
        return "unknown"

    headers = getattr(request, "headers", None) or {}

    # Strategy 1: Render / Cloudflare-backed proxy
    if is_render:
        # Step A: CF-Connecting-IP (tamper-proof from Cloudflare edge)
        cf_ip = headers.get("cf-connecting-ip")
        if cf_ip and isinstance(cf_ip, str):
            cleaned_cf = cf_ip.strip()
            try:
                ipaddress.ip_address(cleaned_cf)
                return cleaned_cf
            except ValueError:
                pass

        # If CF-Connecting-IP is missing or invalid on Render, do NOT blindly trust
        # X-Forwarded-For as it may contain client-manipulated spoofed hops.
        # Fall back safely to direct socket peer.
        if direct_host:
            try:
                ipaddress.ip_address(direct_host)
                return direct_host
            except ValueError:
                pass
        return "unknown"

    # Strategy 2: CIDR Mode (when TRUSTED_PROXIES is configured)
    if trusted_proxies_cfg:
        trusted_nets = _parse_trusted_networks(trusted_proxies_cfg)
        direct_is_trusted = False
        if direct_host:
            try:
                direct_addr = ipaddress.ip_address(direct_host)
                direct_is_trusted = any(direct_addr in net for net in trusted_nets)
            except ValueError:
                direct_is_trusted = False

        # If direct peer is not in trusted proxies, ignore forwarded headers!
        if not direct_is_trusted:
            if direct_host:
                try:
                    ipaddress.ip_address(direct_host)
                    return direct_host
                except ValueError:
                    pass
            return "unknown"

        # Direct peer is verified trusted in CIDR mode. Inspect X-Forwarded-For right-to-left.
        forwarded = headers.get("x-forwarded-for")
        if forwarded and isinstance(forwarded, str):
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            for hop in reversed(hops):
                try:
                    addr = ipaddress.ip_address(hop)
                    if not any(addr in net for net in trusted_nets):
                        return str(addr)
                except ValueError:
                    continue

        # In CIDR mode, accept X-Real-IP ONLY IF direct peer is verified AND TRUST_REAL_IP_HEADER=true
        trust_real_ip = os.getenv("TRUST_REAL_IP_HEADER", "false").strip().lower() in {"1", "true", "yes"}
        real_ip = headers.get("x-real-ip")
        if trust_real_ip and real_ip and isinstance(real_ip, str):
            cleaned_real = real_ip.strip()
            try:
                ipaddress.ip_address(cleaned_real)
                return cleaned_real
            except ValueError:
                pass

        if direct_host:
            try:
                ipaddress.ip_address(direct_host)
                return direct_host
            except ValueError:
                pass
        return "unknown"

    # Strategy 3: Standard Count-Based Proxy Mode (hops[-proxy_count])
    forwarded = headers.get("x-forwarded-for")
    if forwarded and isinstance(forwarded, str):
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            proxy_count = _parse_int_env("TRUSTED_PROXY_COUNT", 1, min_val=1, max_val=10)
            if len(hops) >= proxy_count:
                target_ip = hops[-proxy_count]
                try:
                    ipaddress.ip_address(target_ip)
                    return target_ip
                except ValueError:
                    pass

    # In count-based mode without a verified peer, NEVER trust X-Real-IP as fallback.
    # Fall back safely to direct socket IP.
    if direct_host:
        try:
            ipaddress.ip_address(direct_host)
            return direct_host
        except ValueError:
            pass

    return "unknown"


class SlidingWindowRateLimiter:
    """Thread-safe, process-local sliding-window rate limiter for FastAPI with bounded cleanup,
    partitioned capacity pools (authenticated vs. anonymous), and background asyncio cleanup.

    NOTE: This implementation is process-local. In multi-worker or multi-replica
    environments, each process maintains its own independent window. Distributed
    rate limiting across replicas (e.g. via Redis) is planned for a future phase.
    """

    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._records: Dict[Tuple[str, str], deque] = defaultdict(deque)
        self._user_keys: set[Tuple[str, str]] = set()
        self._anon_keys: set[Tuple[str, str]] = set()
        self._last_cleanup: float = time.monotonic()
        self._cleanup_interval: float = _parse_float_env("RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 60.0, min_val=1.0)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_running: bool = False
        self._redis_client: Optional[Any] = redis_client
        self._redis_attempted_init: bool = False
        self._redis_active: bool = redis_client is not None

    def set_redis_client(self, client: Optional[Any]) -> None:
        """Inject or clear a shared Redis client instance."""
        with self._lock:
            self._redis_client = client
            self._redis_active = client is not None
            self._redis_attempted_init = True

    def is_redis_active(self) -> bool:
        """Check if Redis distributed rate limiting is currently healthy and active."""
        return self._redis_active and self._redis_client is not None

    def get_fail_closed_endpoints(self) -> set[str]:
        """Retrieve set of sensitive endpoints designated for fail-closed policy on Redis failure."""
        raw = os.getenv("RATE_LIMIT_REDIS_FAIL_CLOSED_ENDPOINTS", "auth,ocr,batch")
        return {ep.strip().lower() for ep in raw.split(",") if ep.strip()}

    def is_strict_fail_closed(self, endpoint_key: str) -> bool:
        """Determine whether endpoint should strictly fail-closed rather than fall back to process-local limiting."""
        strict = os.getenv("RATE_LIMIT_STRICT_FAIL_CLOSED", "false").strip().lower() in {"1", "true", "yes"}
        return strict and (endpoint_key.lower() in self.get_fail_closed_endpoints())

    def _get_or_init_redis(self) -> Optional[Any]:
        """Lazily initialize Redis connection if configured via REDIS_URL."""
        if self._redis_client is not None:
            return self._redis_client

        if self._redis_attempted_init:
            return None

        with self._lock:
            if self._redis_attempted_init:
                return self._redis_client
            self._redis_attempted_init = True

            redis_url = os.getenv("REDIS_URL")
            if not redis_url or not redis_url.strip():
                return None

            if redis is None:
                logger.warning(
                    "REDIS_URL is configured, but 'redis' Python package is not installed. "
                    "Falling back to process-local in-memory rate limiting."
                )
                return None

            try:
                client = redis.from_url(
                    redis_url.strip(),
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=1.0,
                )
                client.ping()
                self._redis_client = client
                self._redis_active = True
                logger.info("Successfully connected to Redis distributed rate limiter.")
                try:
                    from services.metrics import record_redis_status
                    record_redis_status(True)
                except Exception:
                    pass
                return self._redis_client
            except Exception as exc:
                logger.warning(
                    "Failed to connect to Redis at %s: %s. Falling back to process-local rate limiting.",
                    redis_url[:32],
                    exc,
                )
                self._redis_active = False
                try:
                    from services.metrics import record_redis_status
                    record_redis_status(False)
                except Exception:
                    pass
                return None

    def _check_redis(
        self,
        endpoint_key: str,
        client_key: str,
        now: float,
    ) -> Tuple[bool, int]:
        """Execute atomic Redis sliding-window Lua script for distributed token allocation."""
        client = self._get_or_init_redis()
        if client is None:
            raise RuntimeError("Redis client is unavailable")

        max_requests, window = self.get_client_threshold(endpoint_key, client_key)
        redis_key = f"ratelimit:{endpoint_key}:{client_key}"

        res = client.eval(
            REDIS_SLIDING_WINDOW_LUA,
            1,
            redis_key,
            str(now),
            str(window),
            str(max_requests),
        )

        if isinstance(res, (list, tuple)) and len(res) >= 2:
            return (bool(res[0]), int(res[1]))
        elif isinstance(res, (int, bool)):
            allowed = bool(res)
            return (allowed, 0 if allowed else window)
        return (False, window)

    def is_enabled(self) -> bool:
        """Check if rate limiting is enabled via environment."""
        val = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
        return val in {"1", "true", "yes"}

    @staticmethod
    def is_authenticated_client(client_key: str) -> bool:
        """Check if client_key represents an authenticated user."""
        return client_key.startswith("user:")

    def get_max_user_records(self) -> int:
        """Retrieve maximum capacity for authenticated user records.

        Configured via RATE_LIMIT_MAX_USER_KEYS. Falls back to RATE_LIMIT_MAX_KEYS,
        defaulting to 5000.
        """
        default_max = _parse_int_env("RATE_LIMIT_MAX_KEYS", 5000, min_val=1)
        return _parse_int_env("RATE_LIMIT_MAX_USER_KEYS", default_max, min_val=1)

    def get_max_anon_records(self) -> int:
        """Retrieve maximum capacity for anonymous client IP records.

        Configured via RATE_LIMIT_MAX_ANON_KEYS. Falls back to RATE_LIMIT_MAX_KEYS,
        defaulting to 5000.
        """
        default_max = _parse_int_env("RATE_LIMIT_MAX_KEYS", 5000, min_val=1)
        return _parse_int_env("RATE_LIMIT_MAX_ANON_KEYS", default_max, min_val=1)

    def get_max_records(self) -> int:
        """Retrieve total combined in-memory key threshold."""
        return self.get_max_user_records() + self.get_max_anon_records()

    def get_limits(self) -> Dict[str, Tuple[int, int]]:
        """Retrieve configured limits: {endpoint_key: (max_requests, window_seconds)}."""
        window = _parse_int_env("RATE_LIMIT_WINDOW_SECONDS", 60, min_val=1)
        return {
            "ocr": (_parse_int_env("RATE_LIMIT_OCR", 5, min_val=1), window),
            "chat": (_parse_int_env("RATE_LIMIT_CHAT", 15, min_val=1), window),
            "scan": (_parse_int_env("RATE_LIMIT_SCAN", 30, min_val=1), window),
            "auth": (_parse_int_env("RATE_LIMIT_AUTH", 5, min_val=1), window),
            "analyze": (_parse_int_env("RATE_LIMIT_ANALYZE", 15, min_val=1), window),
            "compare": (_parse_int_env("RATE_LIMIT_COMPARE", 15, min_val=1), window),
            "search": (_parse_int_env("RATE_LIMIT_SEARCH", 30, min_val=1), window),
            "product": (_parse_int_env("RATE_LIMIT_PRODUCT", 30, min_val=1), window),
            "claims": (_parse_int_env("RATE_LIMIT_CLAIMS", 20, min_val=1), window),
            "ocr_job": (_parse_int_env("RATE_LIMIT_OCR_JOB", 60, min_val=1), window),
            "batch": (_parse_int_env("RATE_LIMIT_BATCH", 10, min_val=1), window),
            "batch_job": (_parse_int_env("RATE_LIMIT_BATCH_JOB", 60, min_val=1), window),
        }

    def get_client_threshold(self, endpoint_key: str, client_key: str) -> Tuple[int, int]:
        """Determine max_requests and window for an endpoint and client."""
        limits = self.get_limits()
        default_window = _parse_int_env("RATE_LIMIT_WINDOW_SECONDS", 60, min_val=1)
        max_requests, window = limits.get(
            endpoint_key,
            (_parse_int_env("RATE_LIMIT_DEFAULT", 60, min_val=1), default_window),
        )

        # Unauthenticated clients (ip:...) on non-auth endpoints can have tighter thresholds
        if client_key.startswith("ip:") and endpoint_key != "auth":
            factor = _parse_float_env("RATE_LIMIT_UNAUTH_FACTOR", 0.5, min_val=0.01, max_val=1.0)
            max_requests = max(1, int(max_requests * factor))

        return max_requests, window

    def cleanup(self, current_time: Optional[float] = None) -> int:
        """Purge stale records whose timestamps are completely outside their active window.

        Returns:
            Number of purged client keys.
        """
        now = current_time if current_time is not None else time.monotonic()
        with self._lock:
            return self._cleanup_locked(now)

    def _sync_key_pools_if_needed(self) -> None:
        """Reconcile key pool sets if out of sync with _records."""
        if len(self._records) != (len(self._user_keys) + len(self._anon_keys)):
            self._user_keys = {k for k in self._records if self.is_authenticated_client(k[1])}
            self._anon_keys = {k for k in self._records if not self.is_authenticated_client(k[1])}

    def _evict_oldest_unblocked(self, pool_keys: set[Tuple[str, str]]) -> bool:
        """Find and evict the single least-recently-active unblocked key in O(N) without sorting.

        Returns:
            True if an unblocked key was found and evicted, False if all keys are blocked.
        """
        oldest_key = None
        oldest_ts = float("inf")
        for k in pool_keys:
            q = self._records.get(k)
            if q is None:
                continue
            max_req, _ = self.get_client_threshold(k[0], k[1])
            if len(q) < max_req:
                last_ts = q[-1] if q else 0.0
                if last_ts < oldest_ts:
                    oldest_ts = last_ts
                    oldest_key = k

        if oldest_key is not None:
            self._records.pop(oldest_key, None)
            pool_keys.discard(oldest_key)
            return True
        return False

    def _cleanup_locked(self, now: float) -> int:
        keys_to_remove = []
        for (ep, client), q in list(self._records.items()):
            _, window = self.get_client_threshold(ep, client)
            cutoff = now - window
            while q and q[0] <= cutoff:
                q.popleft()
            if not q:
                keys_to_remove.append((ep, client))

        for k in keys_to_remove:
            self._records.pop(k, None)
            self._user_keys.discard(k)
            self._anon_keys.discard(k)

        self._sync_key_pools_if_needed()

        # Enforce authenticated user pool capacity: evict ONLY unblocked user records
        max_user = self.get_max_user_records()
        if len(self._user_keys) > max_user:
            excess_user = len(self._user_keys) - max_user
            unblocked_user = [
                (k, self._records[k][-1] if self._records[k] else 0.0)
                for k in self._user_keys
                if len(self._records[k]) < self.get_client_threshold(k[0], k[1])[0]
            ]
            if unblocked_user:
                unblocked_user.sort(key=lambda item: item[1])
                for k, _ in unblocked_user[:excess_user]:
                    self._records.pop(k, None)
                    self._user_keys.discard(k)

        # Enforce anonymous pool capacity: evict ONLY unblocked anonymous records
        max_anon = self.get_max_anon_records()
        if len(self._anon_keys) > max_anon:
            excess_anon = len(self._anon_keys) - max_anon
            unblocked_anon = [
                (k, self._records[k][-1] if self._records[k] else 0.0)
                for k in self._anon_keys
                if len(self._records[k]) < self.get_client_threshold(k[0], k[1])[0]
            ]
            if unblocked_anon:
                unblocked_anon.sort(key=lambda item: item[1])
                for k, _ in unblocked_anon[:excess_anon]:
                    self._records.pop(k, None)
                    self._anon_keys.discard(k)

        self._last_cleanup = now
        return len(keys_to_remove)

    def check_rate_limit(
        self,
        endpoint_key: str,
        client_key: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, int]:
        """Check and record an access attempt against partitioned capacity pools or distributed Redis.

        Returns:
            (is_allowed, retry_after_seconds)
        """
        if not self.is_enabled():
            return True, 0

        max_requests, window = self.get_client_threshold(endpoint_key, client_key)
        now_epoch = current_time if current_time is not None else time.time()
        now_local = current_time if current_time is not None else time.monotonic()

        # Check distributed Redis rate limit if Redis is configured
        if self._redis_client is not None or (not self._redis_attempted_init and os.getenv("REDIS_URL")):
            try:
                allowed, retry_after = self._check_redis(endpoint_key, client_key, now_epoch)
                self._redis_active = True
                return allowed, retry_after
            except Exception as exc:
                logger.warning(
                    "Distributed Redis rate limiting check failed for endpoint '%s': %s",
                    endpoint_key,
                    exc,
                )
                self._redis_active = False
                try:
                    from services.metrics import record_redis_status
                    record_redis_status(False)
                except Exception:
                    pass

                # Strict fail-closed policy check for sensitive endpoints
                if self.is_strict_fail_closed(endpoint_key):
                    logger.error(
                        "Strict fail-closed rate limit policy activated for endpoint '%s' on Redis failure.",
                        endpoint_key,
                    )
                    return False, window
                # Otherwise, continue below to process-local in-memory sliding window fallback

        now = now_local
        window_start = now - window
        key = (endpoint_key, client_key)
        is_auth = self.is_authenticated_client(client_key)

        with self._lock:
            # Trigger periodic cleanup if interval elapsed
            if (now - self._last_cleanup) >= self._cleanup_interval:
                self._cleanup_locked(now)

            target_pool_keys = self._user_keys if is_auth else self._anon_keys
            max_pool_records = self.get_max_user_records() if is_auth else self.get_max_anon_records()

            # Capacity guard for NEW keys when this specific pool is at or over capacity
            if key not in self._records and len(target_pool_keys) >= max_pool_records:
                # Avoid redundant cleanup storming if cleanup ran in the last 0.5s
                if (now - self._last_cleanup) >= 0.5:
                    self._cleanup_locked(now)

                if key not in self._records and len(target_pool_keys) >= max_pool_records:
                    # Attempt to evict 1 least-recently-active unblocked key from THIS pool
                    evicted = self._evict_oldest_unblocked(target_pool_keys)
                    if not evicted:
                        # Storage pool is saturated with active blocked records.
                        # Do NOT evict any blocked record. Bounded fail-close to prevent memory
                        # exhaustion and prevent attackers from bypassing active blocks.
                        pool_name = "authenticated" if is_auth else "anonymous"
                        logger.warning(
                            "Rate limiter %s capacity reached (%d keys) with all records active/blocked. "
                            "Throttling new client key '%s'.",
                            pool_name,
                            max_pool_records,
                            client_key,
                        )
                        return False, window

            q = self._records[key]
            target_pool_keys.add(key)

            # Purge timestamps outside the active sliding window
            while q and q[0] <= window_start:
                q.popleft()

            if len(q) >= max_requests:
                earliest = q[0]
                retry_after = max(1, math.ceil(window - (now - earliest)))
                return False, retry_after

            q.append(now)
            return True, 0

    def reset(self) -> None:
        """Reset internal rate limit tracking records. Used for test isolation."""
        with self._lock:
            self._records.clear()
            self._user_keys.clear()
            self._anon_keys.clear()
            self._last_cleanup = time.monotonic()
            self._redis_client = None
            self._redis_attempted_init = False
            self._redis_active = False

    async def start_background_cleanup(self, interval: Optional[float] = None) -> None:
        """Start a periodic background asyncio task to purge expired rate-limit records."""
        if interval is not None:
            self._cleanup_interval = max(0.01, float(interval))
        else:
            self._cleanup_interval = _parse_float_env(
                "RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 60.0, min_val=1.0
            )

        if self._cleanup_task is not None and not self._cleanup_task.done():
            logger.debug("Rate limiter background cleanup task is already running.")
            return

        self._cleanup_running = True
        self._cleanup_task = asyncio.create_task(
            self._background_cleanup_loop(),
            name="rate_limiter_background_cleanup",
        )
        logger.info(
            "Rate limiter background cleanup task started with interval %.1fs.",
            self._cleanup_interval,
        )

    async def _background_cleanup_loop(self) -> None:
        """Asynchronous background loop that periodically invokes cleanup()."""
        try:
            while self._cleanup_running:
                await asyncio.sleep(self._cleanup_interval)
                try:
                    purged = self.cleanup()
                    if purged > 0:
                        logger.debug(
                            "Background rate limit cleanup purged %d expired keys.",
                            purged,
                        )
                except Exception as exc:
                    logger.warning("Error during background rate limit cleanup: %s", exc)
        except asyncio.CancelledError:
            logger.debug("Rate limiter background cleanup task received cancellation.")
        finally:
            self._cleanup_running = False

    async def stop_background_cleanup(self) -> None:
        """Gracefully stop and await cancellation of the background cleanup task."""
        self._cleanup_running = False
        task = self._cleanup_task
        self._cleanup_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Error awaiting background cleanup task shutdown: %s", exc)
            logger.info("Rate limiter background cleanup task stopped gracefully.")

    @property
    def is_background_cleanup_running(self) -> bool:
        """Check if the background cleanup task is active and running."""
        return self._cleanup_task is not None and not self._cleanup_task.done()


# Global process-local rate limiter instance
_global_rate_limiter = SlidingWindowRateLimiter()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Retrieve the singleton rate limiter instance."""
    return _global_rate_limiter


def _extract_subnet_key(ip_str: str) -> str:
    """Extract /24 for IPv4 or /64 for IPv6 if subnet aggregation is enabled.

    NOTE: Subnet aggregation should be evaluated carefully in production, as shared
    NAT networks (e.g. mobile APNs, corporate proxies, university Wi-Fi) could
    cause separate legitimate users on the same subnet to share rate limits.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.version == 4:
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            return f"{net.network_address}/24"
        elif ip.version == 6:
            net = ipaddress.ip_network(f"{ip}/64", strict=False)
            return f"{net.network_address}/64"
    except ValueError:
        pass
    return ip_str


def get_client_key(request: Request, user_id: Optional[int] = None) -> str:
    """Determine client identifier: authenticated user ID or validated client IP."""
    if user_id is not None:
        return f"user:{user_id}"

    client_ip = extract_client_ip(request)
    if os.getenv("RATE_LIMIT_SUBNET_AGGREGATION", "false").strip().lower() in {"1", "true", "yes"}:
        client_ip = _extract_subnet_key(client_ip)

    return f"ip:{client_ip}"


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
        try:
            from services.metrics import record_rate_limit_exceeded
            record_rate_limit_exceeded(endpoint_key)
        except Exception:
            pass
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
