from __future__ import annotations

import concurrent.futures
import time
from unittest.mock import MagicMock
import pytest

from services.rate_limiter import (
    SlidingWindowRateLimiter,
    extract_client_ip,
    get_client_key,
    get_rate_limiter,
)


# ==============================================================================
# 1. INDEPENDENT CAPACITY POOL TESTS (AUTHENTICATED VS ANONYMOUS)
# ==============================================================================

def test_anonymous_pool_capacity_bound(monkeypatch):
    """Verify that the anonymous key pool strictly respects RATE_LIMIT_MAX_ANON_KEYS."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "3")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "5")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Insert 10 unblocked anonymous keys
    for i in range(10):
        limiter.check_rate_limit("scan", f"ip:198.51.100.{i}", current_time=t0)

    # Force cleanup pass
    limiter.cleanup(current_time=t0 + 1.0)

    assert len(limiter._anon_keys) <= 3
    assert len(limiter._records) <= 3


def test_authenticated_reserved_capacity_remains_available_during_anon_saturation(monkeypatch):
    """Verify that 100% saturation of the anonymous pool never prevents authenticated users from access."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Flood and block 2 anonymous IPs on /auth (auth limit is 5)
    for _ in range(5):
        limiter.check_rate_limit("auth", "ip:10.0.0.1", current_time=t0)
        limiter.check_rate_limit("auth", "ip:10.0.0.2", current_time=t0)

    # Anonymous pool is now 100% saturated with active blocked records
    assert len(limiter._anon_keys) == 2

    # A 3rd anonymous client is throttled due to anonymous pool saturation
    allowed_anon, retry_anon = limiter.check_rate_limit("auth", "ip:10.0.0.3", current_time=t0 + 1.0)
    assert allowed_anon is False
    assert retry_anon == 60

    # CRITICAL: Authenticated users MUST still be admitted into their reserved pool!
    allowed_u1, retry_u1 = limiter.check_rate_limit("scan", "user:101", current_time=t0 + 1.0)
    assert allowed_u1 is True
    assert retry_u1 == 0

    allowed_u2, retry_u2 = limiter.check_rate_limit("scan", "user:102", current_time=t0 + 1.0)
    assert allowed_u2 is True
    assert retry_u2 == 0

    assert len(limiter._user_keys) == 2
    assert ("scan", "user:101") in limiter._records
    assert ("scan", "user:102") in limiter._records


def test_anonymous_records_never_evict_authenticated_records(monkeypatch):
    """Verify that incoming anonymous requests never evict unblocked or blocked authenticated users."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Authenticated user makes 1 request (sub-threshold / unblocked)
    allowed, _ = limiter.check_rate_limit("scan", "user:active_vip", current_time=t0)
    assert allowed is True

    # Anonymous flood arrives attempting to exhaust storage
    for i in range(10):
        limiter.check_rate_limit("auth", f"ip:203.0.113.{i}", current_time=t0 + 1.0)

    # The authenticated record must NOT have been evicted
    assert ("scan", "user:active_vip") in limiter._records
    assert ("scan", "user:active_vip") in limiter._user_keys

    # Subsequent request by authenticated user is allowed and recognized as existing key
    allowed, _ = limiter.check_rate_limit("scan", "user:active_vip", current_time=t0 + 2.0)
    assert allowed is True
    assert len(limiter._records[("scan", "user:active_vip")]) == 2


def test_authenticated_records_never_evict_anonymous_records(monkeypatch):
    """Verify that authenticated traffic cannot evict records from the anonymous pool."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Anonymous client makes 1 request
    allowed, _ = limiter.check_rate_limit("scan", "ip:198.51.100.5", current_time=t0)
    assert allowed is True

    # Authenticated users flood their pool
    for i in range(10):
        limiter.check_rate_limit("scan", f"user:{i}", current_time=t0 + 1.0)

    # Anonymous record remains untouched
    assert ("scan", "ip:198.51.100.5") in limiter._records
    assert ("scan", "ip:198.51.100.5") in limiter._anon_keys


# ==============================================================================
# 2. BLOCKED STATUS PRESERVATION & CLEANUP
# ==============================================================================

def test_blocked_records_retain_limits_during_pool_pressure(monkeypatch):
    """Verify that an active blocked client cannot unblock themselves via traffic pressure."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Block ip:abuser on /auth
    for _ in range(5):
        limiter.check_rate_limit("auth", "ip:abuser", current_time=t0)

    # Confirm blocked
    allowed, retry = limiter.check_rate_limit("auth", "ip:abuser", current_time=t0)
    assert allowed is False
    assert retry > 0

    # Traffic pressure from other IPs
    for i in range(5):
        limiter.check_rate_limit("auth", f"ip:other_{i}", current_time=t0 + 1.0)

    # ip:abuser MUST still be blocked
    allowed, retry = limiter.check_rate_limit("auth", "ip:abuser", current_time=t0 + 2.0)
    assert allowed is False
    assert retry > 0


def test_expired_records_cleaned_up_in_both_pools(monkeypatch):
    """Verify cleanup() clears expired timestamps and removes empty queues from both pools."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    limiter.check_rate_limit("scan", "user:temp", current_time=t0)
    limiter.check_rate_limit("scan", "ip:temp", current_time=t0)

    assert len(limiter._records) == 2
    assert len(limiter._user_keys) == 1
    assert len(limiter._anon_keys) == 1

    # Window is 60s; advance to t0 + 61.0
    purged = limiter.cleanup(current_time=t0 + 61.0)
    assert purged == 2
    assert len(limiter._records) == 0
    assert len(limiter._user_keys) == 0
    assert len(limiter._anon_keys) == 0


def test_unblocked_records_evicted_in_lru_order_within_pool(monkeypatch):
    """Verify that when a pool is at capacity, the least recently active unblocked key is evicted."""
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # ip:1 active at t0, ip:2 active at t0 + 5.0
    limiter.check_rate_limit("scan", "ip:1", current_time=t0)
    limiter.check_rate_limit("scan", "ip:2", current_time=t0 + 5.0)

    # Pool capacity (2) is reached. New client ip:3 arrives at t0 + 10.0
    allowed, _ = limiter.check_rate_limit("scan", "ip:3", current_time=t0 + 10.0)
    assert allowed is True

    # ip:1 was the least recently active, so it should have been evicted
    assert ("scan", "ip:1") not in limiter._records
    # ip:2 and ip:3 must remain
    assert ("scan", "ip:2") in limiter._records
    assert ("scan", "ip:3") in limiter._records
    assert len(limiter._anon_keys) == 2


# ==============================================================================
# 3. OPTIONAL SUBNET AGGREGATION DEFENSE
# ==============================================================================

def test_subnet_aggregation_ipv4_and_ipv6(monkeypatch):
    """Verify optional subnet aggregation aggregates /24 IPv4 and /64 IPv6 when enabled."""
    # When enabled
    monkeypatch.setenv("RATE_LIMIT_SUBNET_AGGREGATION", "true")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    req_v4_a = MagicMock()
    req_v4_a.client.host = "198.51.100.5"
    req_v4_b = MagicMock()
    req_v4_b.client.host = "198.51.100.99"

    assert get_client_key(req_v4_a) == "ip:198.51.100.0/24"
    assert get_client_key(req_v4_b) == "ip:198.51.100.0/24"
    assert get_client_key(req_v4_a) == get_client_key(req_v4_b)

    req_v6_a = MagicMock()
    req_v6_a.client.host = "2001:db8:abcd:12::1"
    req_v6_b = MagicMock()
    req_v6_b.client.host = "2001:db8:abcd:12::ffff"

    assert get_client_key(req_v6_a) == "ip:2001:db8:abcd:12::/64"
    assert get_client_key(req_v6_b) == "ip:2001:db8:abcd:12::/64"

    # Authenticated user is unaffected by subnet aggregation
    assert get_client_key(req_v4_a, user_id=42) == "user:42"

    # When disabled (default)
    monkeypatch.setenv("RATE_LIMIT_SUBNET_AGGREGATION", "false")
    assert get_client_key(req_v4_a) == "ip:198.51.100.5"
    assert get_client_key(req_v4_b) == "ip:198.51.100.99"


# ==============================================================================
# 4. CONCURRENCY & THREAD SAFETY
# ==============================================================================

def test_concurrent_requests_and_cleanup_thread_safety(monkeypatch):
    """Verify that high-concurrency requests and background cleanup do not cause race conditions."""
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "50")
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "50")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    errors = []

    def make_requests(worker_id: int):
        t = time.time()
        try:
            for i in range(30):
                if worker_id % 2 == 0:
                    client_key = f"user:{worker_id}_{i % 10}"
                else:
                    client_key = f"ip:10.{worker_id}.{i % 10}.1"
                limiter.check_rate_limit("scan", client_key, current_time=t + (i * 0.1))
        except Exception as e:
            errors.append(e)

    def run_cleanup():
        t = time.time()
        try:
            for _ in range(10):
                time.sleep(0.01)
                limiter.cleanup(current_time=t + 65.0)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(make_requests, w) for w in range(6)]
        futures.append(executor.submit(run_cleanup))
        futures.append(executor.submit(run_cleanup))
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Thread execution encountered errors: {errors}"
    assert len(limiter._user_keys) <= 50
    assert len(limiter._anon_keys) <= 50
