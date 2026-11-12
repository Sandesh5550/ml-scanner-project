from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import app
from services.rate_limiter import (
    REDIS_SLIDING_WINDOW_LUA,
    SlidingWindowRateLimiter,
    enforce_rate_limit,
    get_rate_limiter,
)
from services.off_client import OFFRateLimiter


class MockRedisClient:
    """In-memory simulation of Redis sorted sets supporting the sliding-window Lua script."""

    def __init__(self) -> None:
        self.sets: Dict[str, List[float]] = {}
        self.expiries: Dict[str, float] = {}
        self.ping_count = 0
        self.should_fail = False
        self.server_time: Optional[float] = None

    def ping(self) -> bool:
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure")
        self.ping_count += 1
        return True

    def eval(self, script: str, numkeys: int, key: str, *args: Any) -> List[int]:
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure during eval")

        # In production Redis, redis.call('TIME') provides server authority
        now = self.server_time if self.server_time is not None else float(args[0])
        window = float(args[1])
        limit = int(args[2])
        clear_before = now - window

        # Retrieve or create sorted set
        scores = self.sets.setdefault(key, [])
        # ZREMRANGEBYSCORE key -inf clear_before
        scores = [s for s in scores if s > clear_before]
        self.sets[key] = scores

        current_count = len(scores)
        if current_count < limit:
            # ZADD key now now:rand
            scores.append(now)
            scores.sort()
            return [1, 0]
        else:
            # ZRANGE key 0 0 WITHSCORES
            oldest_ts = scores[0] if scores else now
            retry_after = max(1, math.ceil(window - (now - oldest_ts)))
            return [0, retry_after]

    def setex(self, key: str, ttl: int, value: str) -> bool:
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure during setex")
        self.sets[key] = [time.time() + ttl]
        return True

    def exists(self, key: str) -> bool:
        if self.should_fail:
            raise ConnectionError("Simulated Redis connection failure during exists")
        return key in self.sets


@pytest.fixture(autouse=True)
def cleanup_rate_limiter():
    limiter = get_rate_limiter()
    limiter.reset()
    yield
    limiter.reset()


def test_redis_sliding_window_lua_allow_and_block():
    mock_redis = MockRedisClient()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)

    # For authenticated users, search endpoint default is 30 req/min
    base_time = 1000.0
    for i in range(30):
        allowed, retry_after = limiter.check_rate_limit("search", "user:100", current_time=base_time + i * 0.1)
        assert allowed is True
        assert retry_after == 0

    # 31st request exceeds limit
    allowed, retry_after = limiter.check_rate_limit("search", "user:100", current_time=base_time + 5.0)
    assert allowed is False
    assert retry_after > 0


def test_multi_worker_shared_redis_state():
    """Verify that multiple distinct worker processes sharing the same Redis instance count toward the same window."""
    shared_redis = MockRedisClient()
    worker1_limiter = SlidingWindowRateLimiter(redis_client=shared_redis)
    worker2_limiter = SlidingWindowRateLimiter(redis_client=shared_redis)

    # Rate limit for auth is 5 req / 60s
    base_time = 2000.0
    # Worker 1 issues 3 requests
    for _ in range(3):
        allowed, _ = worker1_limiter.check_rate_limit("auth", "ip:192.168.1.100", current_time=base_time)
        assert allowed is True

    # Worker 2 issues 2 requests -> hits limit (total 5)
    for _ in range(2):
        allowed, _ = worker2_limiter.check_rate_limit("auth", "ip:192.168.1.100", current_time=base_time)
        assert allowed is True

    # Worker 1 attempts 6th request -> must be blocked due to shared Redis state
    allowed, retry_after = worker1_limiter.check_rate_limit("auth", "ip:192.168.1.100", current_time=base_time)
    assert allowed is False
    assert retry_after >= 1

    # Worker 2 attempts 7th request -> also blocked
    allowed, retry_after = worker2_limiter.check_rate_limit("auth", "ip:192.168.1.100", current_time=base_time)
    assert allowed is False
    assert retry_after >= 1


def test_redis_failure_fallback_to_local_sliding_window(monkeypatch):
    """When Redis fails and strict fail-closed is not active, fallback to local sliding window."""
    monkeypatch.delenv("RATE_LIMIT_STRICT_FAIL_CLOSED", raising=False)
    mock_redis = MockRedisClient()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)

    # First request succeeds via Redis
    allowed, _ = limiter.check_rate_limit("scan", "ip:172.16.0.5", current_time=100.0)
    assert allowed is True

    # Make Redis fail
    mock_redis.should_fail = True

    # Should not crash; gracefully falls back to process-local sliding window
    allowed, retry_after = limiter.check_rate_limit("scan", "ip:172.16.0.5", current_time=100.5)
    assert allowed is True
    assert retry_after == 0


def test_redis_failure_strict_fail_closed_policy(monkeypatch):
    """When strict fail-closed is active for sensitive endpoints, block requests on Redis outage."""
    monkeypatch.setenv("RATE_LIMIT_STRICT_FAIL_CLOSED", "true")
    monkeypatch.setenv("RATE_LIMIT_REDIS_FAIL_CLOSED_ENDPOINTS", "auth,ocr,batch")

    mock_redis = MockRedisClient()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)
    mock_redis.should_fail = True

    # 'auth' is sensitive -> fails closed
    allowed, retry_after = limiter.check_rate_limit("auth", "ip:10.0.0.99", current_time=300.0)
    assert allowed is False
    assert retry_after == 60

    # 'search' is not in fail-closed endpoints -> falls back to local limiter and passes
    allowed, _ = limiter.check_rate_limit("search", "ip:10.0.0.99", current_time=300.0)
    assert allowed is True


def test_enforce_rate_limit_sets_retry_after_header():
    mock_redis = MockRedisClient()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)

    req = MagicMock()
    req.headers = {}
    req.client.host = "10.0.0.50"

    # Exhaust 'auth' limit (5 requests)
    for _ in range(5):
        enforce_rate_limit("auth", req, user_id=42, limiter=limiter)

    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit("auth", req, user_id=42, limiter=limiter)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert int(exc_info.value.headers["Retry-After"]) >= 1


def test_off_rate_limiter_with_redis():
    mock_redis = MockRedisClient()
    limiter = OFFRateLimiter(redis_client=mock_redis)
    assert limiter.is_redis_coordinated is True


def test_rate_limiter_never_passes_monotonic_to_redis():
    """Verify that check_rate_limit never passes monotonic timestamps to Redis."""
    captured_args = []

    class InspectingRedis(MockRedisClient):
        def eval(self, script: str, numkeys: int, key: str, *args: Any):
            captured_args.append(args)
            return super().eval(script, numkeys, key, *args)

    mock_redis = InspectingRedis()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)

    limiter.check_rate_limit("search", "ip:1.2.3.4")
    assert len(captured_args) == 1
    passed_time = float(captured_args[0][0])
    # Epoch time is > 1.7e9 (current decade); monotonic time is typically < 1e7
    assert passed_time > 1_500_000_000, f"Expected epoch timestamp, got {passed_time}"


def test_redis_simulated_container_clock_offsets():
    """Verify rate limiter coordinates across containers with differing system clock offsets."""
    shared_redis = MockRedisClient()
    # Real Redis Lua script prefers redis.call('TIME') server time over container client time
    base_epoch = 1_700_000_000.0
    shared_redis.server_time = base_epoch

    limiter_a = SlidingWindowRateLimiter(redis_client=shared_redis)
    limiter_b = SlidingWindowRateLimiter(redis_client=shared_redis)

    # Container A has +120s offset, Container B has -60s offset
    for _ in range(3):
        allowed, _ = limiter_a.check_rate_limit("auth", "ip:10.10.10.10", current_time=base_epoch + 120.0)
        assert allowed is True

    for _ in range(2):
        allowed, _ = limiter_b.check_rate_limit("auth", "ip:10.10.10.10", current_time=base_epoch - 60.0)
        assert allowed is True

    # 6th request from Container A must be blocked because Redis server time coordinates the 5-req limit
    allowed, retry_after = limiter_a.check_rate_limit("auth", "ip:10.10.10.10", current_time=base_epoch + 125.0)
    assert allowed is False
    assert retry_after >= 1


def test_redis_disconnection_and_reconnection():
    """Verify that when Redis disconnects and reconnects, limiter transitions safely."""
    mock_redis = MockRedisClient()
    limiter = SlidingWindowRateLimiter(redis_client=mock_redis)

    # 1. Connected
    assert limiter.is_redis_active() is True
    allowed, _ = limiter.check_rate_limit("search", "ip:5.5.5.5", current_time=100.0)
    assert allowed is True

    # 2. Redis failure -> falls back to local memory without crashing
    mock_redis.should_fail = True
    allowed, retry_after = limiter.check_rate_limit("search", "ip:5.5.5.5", current_time=101.0)
    assert allowed is True
    assert retry_after == 0
    assert limiter.is_redis_active() is False

    # 3. Redis restored -> re-connects cleanly
    mock_redis.should_fail = False
    allowed, _ = limiter.check_rate_limit("search", "ip:5.5.5.5", current_time=102.0)
    assert allowed is True
    assert limiter.is_redis_active() is True
