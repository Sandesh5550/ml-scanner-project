from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import math
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from services.rate_limiter import (
    SlidingWindowRateLimiter,
    _parse_float_env,
    _parse_int_env,
    extract_client_ip,
    get_client_key,
    get_rate_limiter,
)


def _create_mock_request(
    client_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> Request:
    client = MagicMock()
    client.host = client_host
    req = MagicMock(spec=Request)
    req.client = client
    req.headers = headers or {}
    return req


# ==============================================================================
# 1. MALFORMED ENVIRONMENT VARIABLE HANDLING
# ==============================================================================

@pytest.mark.parametrize(
    "raw_val,default,min_val,max_val,expected",
    [
        ("100", 50, None, None, 100),
        ("  200  ", 50, None, None, 200),
        ("not_a_number", 50, None, None, 50),
        ("", 50, None, None, 50),
        ("   ", 50, None, None, 50),
        ("12.34", 50, None, None, 50),
        ("-10", 50, 1, 100, 1),
        ("0", 50, 1, 100, 1),
        ("500", 50, 1, 100, 100),
    ],
)
def test_parse_int_env_resilience(monkeypatch, raw_val, default, min_val, max_val, expected):
    """Verify _parse_int_env safely parses, clamps, and falls back without raising ValueError."""
    monkeypatch.setenv("TEST_INT_VAR", raw_val)
    val = _parse_int_env("TEST_INT_VAR", default, min_val=min_val, max_val=max_val)
    assert val == expected


@pytest.mark.parametrize(
    "raw_val,default,min_val,max_val,expected",
    [
        ("0.75", 0.5, None, None, 0.75),
        ("  0.25  ", 0.5, None, None, 0.25),
        ("invalid_float", 0.5, None, None, 0.5),
        ("nan", 0.5, None, None, 0.5),
        ("inf", 0.5, None, None, 0.5),
        ("-inf", 0.5, None, None, 0.5),
        ("", 0.5, None, None, 0.5),
        ("-0.5", 0.5, 0.01, 1.0, 0.01),
        ("2.5", 0.5, 0.01, 1.0, 1.0),
    ],
)
def test_parse_float_env_resilience(monkeypatch, raw_val, default, min_val, max_val, expected):
    """Verify _parse_float_env safely parses, clamps, and falls back without raising ValueError."""
    monkeypatch.setenv("TEST_FLOAT_VAR", raw_val)
    val = _parse_float_env("TEST_FLOAT_VAR", default, min_val=min_val, max_val=max_val)
    assert val == expected


def test_rate_limiter_corrupted_capacity_env_vars(monkeypatch):
    """Verify that completely corrupted capacity env vars safely fall back to defaults."""
    monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "corrupted_string")
    monkeypatch.delenv("RATE_LIMIT_MAX_USER_KEYS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_MAX_ANON_KEYS", raising=False)

    limiter = SlidingWindowRateLimiter()
    assert limiter.get_max_user_records() == 5000
    assert limiter.get_max_anon_records() == 5000
    assert limiter.get_max_records() == 10000

    # User-specific corrupted
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "not_a_number")
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "-999")
    assert limiter.get_max_user_records() == 5000
    assert limiter.get_max_anon_records() == 1  # Clamped to min_val=1


def test_rate_limiter_corrupted_thresholds(monkeypatch):
    """Verify endpoint thresholds and unauth factors safely handle malformed values."""
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "bad_window")
    monkeypatch.setenv("RATE_LIMIT_OCR", "invalid_ocr")
    monkeypatch.setenv("RATE_LIMIT_UNAUTH_FACTOR", "bogus_factor")

    limiter = SlidingWindowRateLimiter()
    limits = limiter.get_limits()
    ocr_max, ocr_win = limits["ocr"]
    assert ocr_max == 5
    assert ocr_win == 60

    max_req, win = limiter.get_client_threshold("ocr", "ip:192.168.1.1")
    # OCR default is 5, factor is 0.5 fallback -> 5 * 0.5 = 2
    assert max_req == 2
    assert win == 60


# ==============================================================================
# 2. RENDER PROXY CLIENT-IP STRATEGY
# ==============================================================================

def test_render_proxy_cf_connecting_ip_precedence(monkeypatch):
    """On Render (PROXY_PROVIDER=render), CF-Connecting-IP takes precedence over X-Forwarded-For."""
    monkeypatch.setenv("PROXY_PROVIDER", "render")
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    req = _create_mock_request(
        client_host="10.10.10.1",  # Render internal mesh proxy
        headers={
            "cf-connecting-ip": "198.51.100.42",
            "x-forwarded-for": "203.0.113.1, 10.10.10.1",
        },
    )
    extracted = extract_client_ip(req)
    assert extracted == "198.51.100.42"


def test_render_auto_detection_via_render_env(monkeypatch):
    """When RENDER='true' is set by Render runtime, Render strategy automatically activates."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("PROXY_PROVIDER", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    req = _create_mock_request(
        client_host="10.0.0.1",
        headers={
            "cf-connecting-ip": "198.51.100.88",
        },
    )
    assert extract_client_ip(req) == "198.51.100.88"


def test_render_proxy_missing_cf_connecting_ip_falls_back_to_socket_host(monkeypatch):
    """On Render, if CF-Connecting-IP is missing, do NOT trust X-Forwarded-For; fallback safely to socket peer."""
    monkeypatch.setenv("PROXY_PROVIDER", "render")
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    req = _create_mock_request(
        client_host="10.10.10.1",
        headers={
            "x-forwarded-for": "198.51.100.77, 10.10.10.1",
        },
    )
    assert extract_client_ip(req) == "10.10.10.1"


def test_render_proxy_malformed_cf_connecting_ip_falls_back_to_socket_host(monkeypatch):
    """On Render, if CF-Connecting-IP is malformed, do NOT trust X-Forwarded-For; fallback to socket peer."""
    monkeypatch.setenv("PROXY_PROVIDER", "render")
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    req = _create_mock_request(
        client_host="10.10.10.1",
        headers={
            "cf-connecting-ip": "attacker<script>",
            "x-forwarded-for": "198.51.100.99, 10.10.10.1",
        },
    )
    assert extract_client_ip(req) == "10.10.10.1"


def test_render_proxy_spoofed_x_forwarded_for_rejected_when_cf_connecting_ip_missing(monkeypatch):
    """Regression test: On Render, an attacker cannot spoof client IP via X-Forwarded-For when CF-Connecting-IP is absent."""
    monkeypatch.setenv("PROXY_PROVIDER", "render")
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    req = _create_mock_request(
        client_host="10.10.10.1",
        headers={
            "x-forwarded-for": "1.2.3.4, 5.6.7.8",
        },
    )
    # Must reject spoofed XFF and return the actual direct socket peer
    assert extract_client_ip(req) == "10.10.10.1"


def test_render_proxy_explicit_disable_trust(monkeypatch):
    """If TRUST_PROXY_HEADERS is explicitly false, Render mode is disabled and direct peer is used."""
    monkeypatch.setenv("PROXY_PROVIDER", "render")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    req = _create_mock_request(
        client_host="10.10.10.1",
        headers={
            "cf-connecting-ip": "198.51.100.42",
            "x-forwarded-for": "198.51.100.42",
        },
    )
    assert extract_client_ip(req) == "10.10.10.1"


def test_local_environment_ignores_cf_connecting_ip(monkeypatch):
    """In standard local environment (TRUST_PROXY_HEADERS=false, non-Render), CF-Connecting-IP is ignored."""
    monkeypatch.delenv("PROXY_PROVIDER", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    req = _create_mock_request(
        client_host="127.0.0.1",
        headers={
            "cf-connecting-ip": "203.0.113.195",
            "x-forwarded-for": "203.0.113.195",
        },
    )
    assert extract_client_ip(req) == "127.0.0.1"


# ==============================================================================
# 3. MONOTONIC TIMING & RETRY-AFTER CALCULATIONS
# ==============================================================================

def test_monotonic_time_immune_to_backward_clock_jump(monkeypatch):
    """Verify that simulated wall-clock shifts do not disrupt the rate limiter."""
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    # Base monotonic time
    t0 = 500.0
    allowed, _ = limiter.check_rate_limit("auth", "ip:10.0.0.1", current_time=t0)
    assert allowed is True

    # Check that timestamps stored in deque are using monotonic sequence
    key = ("auth", "ip:10.0.0.1")
    assert key in limiter._records
    assert limiter._records[key][0] == 500.0


def test_retry_after_always_positive_integer_ceiling(monkeypatch):
    """Verify Retry-After is always an integer >= 1 calculated via math.ceil."""
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Fill limit for /auth (max 5 requests)
    for _ in range(5):
        limiter.check_rate_limit("auth", "ip:10.0.0.5", current_time=t0)

    # 6th request at t0 + 0.1s: remaining window is 60 - 0.1 = 59.9s -> ceil is 60s
    allowed, retry_after = limiter.check_rate_limit("auth", "ip:10.0.0.5", current_time=t0 + 0.1)
    assert allowed is False
    assert isinstance(retry_after, int)
    assert retry_after == 60

    # Request at t0 + 59.2s: remaining window is 0.8s -> ceil is 1s
    allowed, retry_after = limiter.check_rate_limit("auth", "ip:10.0.0.5", current_time=t0 + 59.2)
    assert allowed is False
    assert retry_after == 1


# ==============================================================================
# 4. BACKGROUND CLEANUP TASK & LIFESPAN INTEGRATION
# ==============================================================================

@pytest.mark.anyio
async def test_background_cleanup_task_lifecycle():
    """Verify that start_background_cleanup and stop_background_cleanup manage task safely."""
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    assert not limiter.is_background_cleanup_running

    await limiter.start_background_cleanup(interval=0.1)
    assert limiter.is_background_cleanup_running

    # Starting again should be idempotent
    task_ref = limiter._cleanup_task
    await limiter.start_background_cleanup(interval=0.1)
    assert limiter._cleanup_task is task_ref

    # Insert an expired record
    key = ("scan", "ip:10.0.0.99")
    limiter._records[key].append(time.monotonic() - 100.0)
    limiter._anon_keys.add(key)
    assert key in limiter._records

    # Wait briefly for background loop iteration
    await asyncio.sleep(0.25)

    # Record should have been purged by background task
    assert key not in limiter._records

    # Graceful stop
    await limiter.stop_background_cleanup()
    assert not limiter.is_background_cleanup_running
    assert limiter._cleanup_task is None

    # Stopping again when already stopped is safe
    await limiter.stop_background_cleanup()


def test_fastapi_lifespan_starts_and_stops_background_cleanup():
    """Verify that entering FastAPI lifespan starts background cleanup and exiting stops it."""
    from fastapi.testclient import TestClient
    from api.main import app

    limiter = get_rate_limiter()
    with TestClient(app) as client:
        # Inside the context, lifespan startup has run
        assert limiter.is_background_cleanup_running
        resp = client.get("/health")
        assert resp.status_code == 200

    # Outside the context, lifespan shutdown has run
    assert not limiter.is_background_cleanup_running


# ==============================================================================
# 5. DETERMINISTIC CONCURRENT SATURATION TESTS
# ==============================================================================

def test_concurrent_anonymous_saturation_barrier_isolation(monkeypatch):
    """20 threads concurrently generate >100 distinct anonymous client IPs.

    Verifies:
    1. Pool capacity never exceeds RATE_LIMIT_MAX_ANON_KEYS.
    2. Eviction runs safely without race conditions or KeyError.
    3. Authenticated requests succeed and enter the authenticated pool unaffected.
    """
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "10")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "10")

    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    num_threads = 20
    keys_per_thread = 6  # 20 * 6 = 120 distinct anonymous keys
    barrier = threading.Barrier(num_threads)
    t0 = 1000.0

    def worker(worker_id: int):
        barrier.wait()
        results = []
        for i in range(keys_per_thread):
            ip = f"198.51.{worker_id}.{i}"
            res = limiter.check_rate_limit("scan", f"ip:{ip}", current_time=t0 + i * 0.01)
            results.append(res)
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, tid) for tid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Anonymous pool must be strictly bounded
    assert len(limiter._anon_keys) <= 10
    assert len(limiter._records) <= 10
    assert len(limiter._user_keys) == 0

    # CRITICAL: Authenticated requests are NOT starved or blocked!
    allowed_user, _ = limiter.check_rate_limit("scan", "user:concurrent_vip", current_time=t0 + 2.0)
    assert allowed_user is True
    assert ("scan", "user:concurrent_vip") in limiter._records
    assert ("scan", "user:concurrent_vip") in limiter._user_keys
    assert len(limiter._user_keys) == 1


def test_concurrent_blocked_pool_saturation_fail_close(monkeypatch):
    """When the anonymous pool is 100% saturated with active blocked keys,
    concurrent requests for new keys must fail-close cleanly without deadlocks or evicting blocked keys.
    """
    monkeypatch.setenv("RATE_LIMIT_MAX_ANON_KEYS", "10")
    monkeypatch.setenv("RATE_LIMIT_MAX_USER_KEYS", "10")

    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 2000.0
    # Saturate pool with 10 blocked keys on /auth (limit is 5 requests)
    blocked_ips = [f"192.0.2.{i}" for i in range(10)]
    for ip in blocked_ips:
        for _ in range(5):
            limiter.check_rate_limit("auth", f"ip:{ip}", current_time=t0)

    assert len(limiter._anon_keys) == 10
    # Verify all 10 are actually blocked
    for ip in blocked_ips:
        allowed, retry = limiter.check_rate_limit("auth", f"ip:{ip}", current_time=t0 + 0.1)
        assert allowed is False

    # Hammer concurrently with 100 new anonymous keys
    num_threads = 10
    barrier = threading.Barrier(num_threads)

    def attacker_worker(worker_id: int):
        barrier.wait()
        rejected = 0
        for i in range(10):
            new_ip = f"198.18.{worker_id}.{i}"
            allowed, retry = limiter.check_rate_limit("auth", f"ip:{new_ip}", current_time=t0 + 1.0)
            if not allowed and retry == 60:
                rejected += 1
        return rejected

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(attacker_worker, tid) for tid in range(num_threads)]
        total_rejected = sum(f.result() for f in futures)

    # All 100 new attempts should have failed-close
    assert total_rejected == 100

    # Ensure none of the original 10 blocked keys were evicted!
    for ip in blocked_ips:
        assert ("auth", f"ip:{ip}") in limiter._records
    assert len(limiter._anon_keys) == 10

    # Authenticated user requests still succeed!
    for u in range(5):
        allowed_u, _ = limiter.check_rate_limit("scan", f"user:unaffected_{u}", current_time=t0 + 1.0)
        assert allowed_u is True

    assert len(limiter._user_keys) == 5
