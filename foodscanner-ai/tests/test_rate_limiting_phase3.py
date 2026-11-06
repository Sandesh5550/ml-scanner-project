from __future__ import annotations

import base64
import time
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from api.main import app
from database.orm import init_db as orm_init_db
from services.rate_limiter import (
    SlidingWindowRateLimiter,
    enforce_rate_limit,
    extract_client_ip,
    get_client_key,
    get_rate_limiter,
)


@pytest.fixture
def client():
    orm_init_db()
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Register and authenticate a unique test user."""
    email = f"rate_limit_phase3_{int(time.time() * 1000)}@example.com"
    reg_resp = client.post(
        "/register",
        json={"email": email, "password": "TestPassword123!", "name": "Rate User"},
    )
    token = reg_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. TRUSTED & UNTRUSTED PROXY IP EXTRACTION TESTS
# ==============================================================================

def test_untrusted_proxy_headers_ignored_when_disabled(monkeypatch):
    """When TRUST_PROXY_HEADERS is false, forwarded headers must be ignored entirely."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}
    mock_req.client.host = "198.51.100.5"

    extracted = extract_client_ip(mock_req)
    assert extracted == "198.51.100.5"
    assert get_client_key(mock_req) == "ip:198.51.100.5"


def test_trusted_proxy_count_1_extracts_appended_client_ip(monkeypatch):
    """When behind 1 trusted proxy, proxy appends connecting peer IP; extract hops[-1]."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")

    # Client (203.0.113.195) sent spoofed '1.1.1.1'; reverse proxy appended client IP to header
    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "1.1.1.1, 203.0.113.195"}
    mock_req.client.host = "70.41.3.18"

    extracted = extract_client_ip(mock_req)
    assert extracted == "203.0.113.195"
    assert get_client_key(mock_req) == "ip:203.0.113.195"


def test_trusted_proxy_count_2_extracts_correct_hop(monkeypatch):
    """When behind 2 trusted proxies (e.g. CDN -> Reverse Proxy -> App), extract hops[-2]."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "2")

    # Client (203.0.113.195) -> CDN (198.51.100.2) -> Proxy (70.41.3.18) -> App
    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "1.1.1.1, 203.0.113.195, 198.51.100.2"}
    mock_req.client.host = "70.41.3.18"

    extracted = extract_client_ip(mock_req)
    assert extracted == "203.0.113.195"
    assert get_client_key(mock_req) == "ip:203.0.113.195"


def test_insufficient_proxy_hops_falls_back_to_direct_host(monkeypatch):
    """When header has fewer hops than TRUSTED_PROXY_COUNT, fall back safely to direct socket."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "3")

    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}
    mock_req.client.host = "192.168.1.10"

    extracted = extract_client_ip(mock_req)
    assert extracted == "192.168.1.10"


def test_malformed_target_proxy_hop_falls_back_to_direct_host(monkeypatch):
    """When the selected proxy hop is a malformed IP, fall back safely to direct socket."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")

    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "1.1.1.1, not-a-valid-ip"}
    mock_req.client.host = "192.168.1.10"

    extracted = extract_client_ip(mock_req)
    assert extracted == "192.168.1.10"


def test_trusted_proxy_cidr_network_filtering(monkeypatch):
    """When TRUSTED_PROXIES CIDR list is set, only requests from trusted peers are unwrapped."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8, 172.16.0.0/12")

    # Case A: Legitimate connection arriving from trusted internal proxy (10.0.0.1)
    req_trusted = MagicMock()
    req_trusted.headers = {"x-forwarded-for": "198.51.100.77, 10.0.0.2"}
    req_trusted.client.host = "10.0.0.1"

    assert extract_client_ip(req_trusted) == "198.51.100.77"

    # Case B: Direct connection arriving from untrusted public IP pretending to be a proxy
    req_untrusted = MagicMock()
    req_untrusted.headers = {"x-forwarded-for": "10.0.0.99"}
    req_untrusted.client.host = "203.0.113.9"

    # Must reject the forwarded header because the direct peer is untrusted
    assert extract_client_ip(req_untrusted) == "203.0.113.9"


def test_malformed_forwarded_headers_rejected_safely(monkeypatch):
    """Malformed non-IP header values must never pass as client keys."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")

    mock_req = MagicMock()
    mock_req.headers = {"x-forwarded-for": "<script>alert(1)</script>, not-an-ip"}
    mock_req.client.host = "192.168.1.10"

    assert extract_client_ip(mock_req) == "192.168.1.10"


def test_x_real_ip_ignored_for_untrusted_direct_client(monkeypatch):
    """Untrusted direct clients cannot spoof identity via X-Real-IP."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")

    mock_req = MagicMock()
    mock_req.headers = {"x-real-ip": "1.2.3.4"}
    mock_req.client.host = "198.51.100.5"

    assert extract_client_ip(mock_req) == "198.51.100.5"


def test_x_real_ip_ignored_in_count_mode_without_verified_peer(monkeypatch):
    """In count-based mode (no TRUSTED_PROXIES), X-Real-IP is never trusted."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)

    mock_req = MagicMock()
    mock_req.headers = {"x-real-ip": "1.2.3.4"}
    mock_req.client.host = "198.51.100.5"

    assert extract_client_ip(mock_req) == "198.51.100.5"


def test_x_real_ip_accepted_only_when_peer_trusted_and_explicitly_configured(monkeypatch):
    """In CIDR mode, X-Real-IP is accepted ONLY when direct peer is trusted and TRUST_REAL_IP_HEADER=true."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")

    mock_req = MagicMock()
    mock_req.headers = {"x-real-ip": "198.51.100.42"}
    mock_req.client.host = "10.0.0.1"

    # Disabled by default
    monkeypatch.delenv("TRUST_REAL_IP_HEADER", raising=False)
    assert extract_client_ip(mock_req) == "10.0.0.1"

    # Explicitly enabled
    monkeypatch.setenv("TRUST_REAL_IP_HEADER", "true")
    assert extract_client_ip(mock_req) == "198.51.100.42"


def test_x_real_ip_malformed_falls_back_to_direct_host(monkeypatch):
    """Malformed X-Real-IP falls back safely to direct socket host."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    monkeypatch.setenv("TRUST_REAL_IP_HEADER", "true")

    mock_req = MagicMock()
    mock_req.headers = {"x-real-ip": "malformed.ip.value"}
    mock_req.client.host = "10.0.0.1"

    assert extract_client_ip(mock_req) == "10.0.0.1"


# ==============================================================================
# 2. BOUNDED IN-MEMORY STORAGE & CLEANUP TESTS
# ==============================================================================

def test_rate_limiter_cleanup_purges_expired_records():
    """Verify that cleanup() removes client records whose windows have fully elapsed."""
    limiter = SlidingWindowRateLimiter()
    t0 = 1000.0

    # Add records at t0
    limiter.check_rate_limit("scan", "user:1", current_time=t0)
    limiter.check_rate_limit("scan", "user:2", current_time=t0)
    limiter.check_rate_limit("scan", "user:3", current_time=t0 + 50.0)

    assert len(limiter._records) == 3

    # Clean up at t0 + 61s (user:1 and user:2 expired based on 60s window; user:3 still active)
    purged = limiter.cleanup(current_time=t0 + 61.0)
    assert purged == 2
    assert len(limiter._records) == 1
    assert ("scan", "user:3") in limiter._records


def test_rate_limiter_enforces_max_record_cap(monkeypatch):
    """Verify that storage cannot exceed RATE_LIMIT_MAX_KEYS under high key diversity."""
    monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "5")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Insert 10 distinct client keys within active window
    for i in range(10):
        limiter.check_rate_limit("scan", f"user:{i}", current_time=t0)

    # Force cleanup pass
    limiter.cleanup(current_time=t0 + 1.0)

    # Total stored keys must be bounded to max_records
    assert len(limiter._records) <= 5


def test_blocked_rate_limit_record_not_evicted_under_capacity_pressure(monkeypatch):
    """Verify that active blocked rate-limit records are never evicted when capacity is reached."""
    monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # user:blocked makes 5 requests on auth endpoint (auth limit is 5) -> blocked!
    for _ in range(5):
        allowed, _ = limiter.check_rate_limit("auth", "user:blocked", current_time=t0)
        assert allowed is True

    # 6th attempt is blocked
    allowed, retry = limiter.check_rate_limit("auth", "user:blocked", current_time=t0)
    assert allowed is False
    assert retry > 0

    # Another client makes 1 sub-threshold request (unblocked)
    allowed, _ = limiter.check_rate_limit("auth", "user:unblocked", current_time=t0 + 1.0)
    assert allowed is True

    # Attacker tries to flood distinct new client keys to evict user:blocked
    for i in range(10):
        limiter.check_rate_limit("auth", f"attacker:{i}", current_time=t0 + 2.0)

    # user:blocked MUST NOT have been evicted; it must still be active and blocked!
    assert ("auth", "user:blocked") in limiter._records
    allowed, retry = limiter.check_rate_limit("auth", "user:blocked", current_time=t0 + 3.0)
    assert allowed is False
    assert retry > 0


def test_capacity_saturation_all_blocked_throttles_new_keys(monkeypatch):
    """When all storage slots are filled with active blocked records, new keys are throttled without unblocking abusers."""
    monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    # Block both user:abuser_1 and user:abuser_2
    for _ in range(5):
        limiter.check_rate_limit("auth", "user:abuser_1", current_time=t0)
        limiter.check_rate_limit("auth", "user:abuser_2", current_time=t0)

    assert len(limiter._records) == 2

    # A new client arrives. Since all stored records are active and blocked,
    # it must NOT evict abuser_1 or abuser_2, and the new key must be throttled safely.
    allowed, retry = limiter.check_rate_limit("auth", "user:new", current_time=t0 + 1.0)
    assert allowed is False
    assert retry == 60  # Default window
    assert len(limiter._records) == 2
    assert ("auth", "user:abuser_1") in limiter._records
    assert ("auth", "user:abuser_2") in limiter._records

    # Confirm abuser_1 is still blocked
    allowed, retry = limiter.check_rate_limit("auth", "user:abuser_1", current_time=t0 + 2.0)
    assert allowed is False
    assert retry > 0


def test_expired_blocked_records_are_evicted_after_window_passes(monkeypatch):
    """When a blocked client's window elapses, cleanup releases their slot safely."""
    monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "2")
    limiter = SlidingWindowRateLimiter()
    limiter.reset()

    t0 = 1000.0
    for _ in range(5):
        limiter.check_rate_limit("auth", "user:temp_blocked", current_time=t0)

    assert ("auth", "user:temp_blocked") in limiter._records

    # At t0 + 61.0, the 60s sliding window has completely elapsed
    limiter.cleanup(current_time=t0 + 61.0)
    assert ("auth", "user:temp_blocked") not in limiter._records

    # A new key can now be admitted without issue
    allowed, _ = limiter.check_rate_limit("auth", "user:fresh", current_time=t0 + 61.0)
    assert allowed is True


# ==============================================================================
# 3. AUTHENTICATED VS UNAUTHENTICATED THRESHOLD TESTS
# ==============================================================================

def test_unauthenticated_requests_have_stricter_factor(monkeypatch):
    """Verify unauthenticated client IPs receive reduced limits via unauth factor."""
    monkeypatch.setenv("RATE_LIMIT_UNAUTH_FACTOR", "0.5")
    monkeypatch.setenv("RATE_LIMIT_SEARCH", "20")
    limiter = SlidingWindowRateLimiter()

    auth_limit, _ = limiter.get_client_threshold("search", "user:10")
    unauth_limit, _ = limiter.get_client_threshold("search", "ip:203.0.113.1")

    assert auth_limit == 20
    assert unauth_limit == 10  # 20 * 0.5


# ==============================================================================
# 4. ROUTE ENFORCEMENT ON EXPENSIVE ENDPOINTS
# ==============================================================================

def test_analyze_rate_limit_enforced(client, auth_headers):
    """Verify POST /analyze enforces rate limiting and returns 429 with Retry-After."""
    limiter = get_rate_limiter()
    limiter.reset()

    payload = {
        "product_name": "Test Energy Bar",
        "calories": 250,
        "sugar": 12,
        "protein": 10,
    }

    # Configured analyze limit is 15
    for i in range(15):
        resp = client.post("/analyze", json=payload, headers=auth_headers)
        assert resp.status_code == 200, f"Attempt {i+1} failed with {resp.status_code}"

    # 16th call must be blocked
    resp_blocked = client.post("/analyze", json=payload, headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for analyze" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_compare_rate_limit_enforced(client, auth_headers):
    """Verify POST /compare enforces rate limiting."""
    limiter = get_rate_limiter()
    limiter.reset()

    payload = {"product_a": "Oats", "product_b": "Muesli"}

    # Configured compare limit is 15
    for i in range(15):
        resp = client.post("/compare", json=payload, headers=auth_headers)
        assert resp.status_code in (200, 404)

    resp_blocked = client.post("/compare", json=payload, headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for compare" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_search_rate_limit_enforced(client, auth_headers):
    """Verify GET /search enforces rate limiting."""
    limiter = get_rate_limiter()
    limiter.reset()

    # Configured search limit is 30
    for i in range(30):
        resp = client.get("/search?query=biscuit", headers=auth_headers)
        assert resp.status_code == 200

    resp_blocked = client.get("/search?query=biscuit", headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for search" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_product_barcode_rate_limit_enforced(client, auth_headers):
    """Verify GET /product/{barcode} enforces rate limiting."""
    limiter = get_rate_limiter()
    limiter.reset()

    # Configured product limit is 30
    for i in range(30):
        resp = client.get("/product/8901058851304", headers=auth_headers)
        assert resp.status_code in (200, 404)

    resp_blocked = client.get("/product/8901058851304", headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for product" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_verify_claims_rate_limit_enforced(client, auth_headers):
    """Verify POST /verify-claims enforces rate limiting."""
    limiter = get_rate_limiter()
    limiter.reset()

    payload = {
        "claims": ["High Protein"],
        "nutrition": {"protein": 15},
    }

    # Configured claims limit is 20
    for i in range(20):
        resp = client.post("/verify-claims", json=payload, headers=auth_headers)
        assert resp.status_code == 200

    resp_blocked = client.post("/verify-claims", json=payload, headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for claims" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_ocr_job_status_rate_limit_enforced(client, auth_headers):
    """Verify GET /ocr/jobs/{job_id} enforces rate limiting."""
    limiter = get_rate_limiter()
    limiter.reset()

    # Configured ocr_job limit is 60
    for i in range(60):
        resp = client.get("/ocr/jobs/ocr_nonexistent_job_123", headers=auth_headers)
        assert resp.status_code in (200, 404)

    resp_blocked = client.get("/ocr/jobs/ocr_nonexistent_job_123", headers=auth_headers)
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded for ocr_job" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers
