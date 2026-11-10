from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import email.utils
import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from schemas.product import CanonicalProduct, DataConfidence, DataProvenance, Nutrition100g
from services.off_client import (
    DEFAULT_CACHE_TTL,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_GLOBAL_PRODUCT_LIMIT,
    DEFAULT_GLOBAL_SEARCH_LIMIT,
    OFFLRUCache,
    OFFRateLimiter,
    OFFResult,
    OFFSearchResult,
    OFFStatus,
    OpenFoodFactsClient,
    canonical_to_legacy_dict,
    normalize_barcode,
    normalize_off_to_canonical,
    parse_retry_after,
    to_optional_float,
)
from services.openfoodfacts_service import (
    _normalize_product_dict,
    _to_float,
    fetch_product_by_barcode,
    fetch_product_by_barcode_async,
    fetch_product_by_barcode_v2,
    fetch_product_by_barcode_v2_async,
)


# ==============================================================================
# SAMPLE TEST PAYLOADS
# ==============================================================================

MOCK_V3_PRODUCT_PAYLOAD = {
    "code": "8901058851304",
    "status": "success",
    "result": {"id": "product_found", "name": "Product found"},
    "product": {
        "product_name": "Maggi 2-Minute Noodles",
        "brands": "Nestle",
        "categories": "Noodles, Instant noodles",
        "serving_size": "70 g",
        "ingredients_text": "Wheat flour, Palm oil, Salt, Wheat gluten, Mineral",
        "additives_tags": ["en:e508", "en:e412"],
        "nutriscore_grade": "d",
        "nutriments": {
            "energy-kcal_100g": 427.0,
            "fat_100g": 15.7,
            "saturated-fat_100g": 6.8,
            "trans-fat_100g": 0.1,
            "carbohydrates_100g": 63.5,
            "sugars_100g": 2.2,
            "proteins_100g": 8.0,
            "fiber_100g": 3.5,
            "salt_100g": 2.3,
            "sodium_100g": 0.92,
        },
    },
}

MOCK_V2_PRODUCT_PAYLOAD = {
    "code": "8901058851304",
    "status": 1,
    "status_verbose": "product found",
    "product": {
        "product_name": "Maggi 2-Minute Noodles (v2)",
        "brands": "Nestle India",
        "ingredients_text": "Wheat flour, Palm oil, Salt",
        "nutriscore_grade": "d",
        "nutriments": {
            "energy-kcal_100g": 427.0,
            "fat_100g": 15.7,
            "saturated_fat_100g": 6.8,
            "trans_fat_100g": 0.1,
            "sugars_100g": 2.2,
            "salt_100g": 2.3,
            "proteins_100g": 8.0,
            "carbohydrates_100g": 63.5,
        },
    },
}


# ==============================================================================
# 1. PRODUCT LOOKUP TESTS
# ==============================================================================

class TestProductLookup:
    """Tests product retrieval, API v3 handling, v2 fallback, normalization, and validation."""

    def test_valid_v3_response(self):
        client = OpenFoodFactsClient()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = MOCK_V3_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", return_value=mock_resp):
            res = client.get_product("8901058851304")

        assert res.is_success
        assert res.status == OFFStatus.SUCCESS
        assert res.barcode == "8901058851304"
        assert res.endpoint_used == "v3"
        assert res.product is not None
        assert res.product.product_name == "Maggi 2-Minute Noodles"
        assert res.product.brand == "Nestle"
        assert res.product.category == "Noodles"
        assert res.product.nutriscore == "d"
        assert res.product.nutrition.calories == 427.0
        assert res.product.nutrition.fat == 15.7
        assert res.product.nutrition.saturated_fat == 6.8
        assert res.product.nutrition.trans_fat == 0.1
        assert res.product.nutrition.salt == 2.3
        assert res.product.nutrition.sodium == 0.92
        assert res.product.primary_provenance == DataProvenance.OFF_API_V3

    def test_valid_v2_fallback_when_v3_fails(self):
        client = OpenFoodFactsClient(max_retries=1)

        # First call (v3 attempt 1 & 2): 503 error
        mock_503 = MagicMock(status_code=503)
        # Fallback call (v2): 200 OK
        mock_v2 = MagicMock(status_code=200)
        mock_v2.json.return_value = MOCK_V2_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", side_effect=[mock_503, mock_503, mock_v2]):
            res = client.get_product("8901058851304")

        assert res.is_success
        assert res.status == OFFStatus.SUCCESS
        assert res.endpoint_used == "v2"
        assert res.product is not None
        assert res.product.product_name == "Maggi 2-Minute Noodles (v2)"
        assert res.product.primary_provenance == DataProvenance.OFF_API_V2

    def test_product_not_found_404_no_retry_no_fallback(self):
        client = OpenFoodFactsClient(max_retries=2)
        mock_404 = MagicMock(status_code=404)

        with patch("httpx.Client.get", return_value=mock_404) as mock_get:
            res = client.get_product("0000000000000")

        assert res.is_not_found
        assert res.status == OFFStatus.NOT_FOUND
        assert res.http_status == 404
        assert res.product is None
        # Must fast-fail immediately without retries or v2 fallback (exactly 1 attempt)
        assert mock_get.call_count == 1
        assert res.attempts == 1

    def test_product_not_found_200_json_status(self):
        client = OpenFoodFactsClient()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "status": "product_not_found",
            "result": {"id": "product_not_found", "name": "Product not found"},
        }

        with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
            res = client.get_product("9999999999999")

        assert res.is_not_found
        assert res.status == OFFStatus.NOT_FOUND
        assert mock_get.call_count == 1

    def test_invalid_barcode_formats(self):
        client = OpenFoodFactsClient()
        with patch("httpx.Client.get") as mock_get:
            # Empty / None
            res_none = client.get_product(None)
            assert res_none.status == OFFStatus.INVALID_BARCODE

            res_empty = client.get_product("   ")
            assert res_empty.status == OFFStatus.INVALID_BARCODE

            # Too short (< 3 chars)
            res_short = client.get_product("12")
            assert res_short.status == OFFStatus.INVALID_BARCODE

            # Non-alphanumeric symbols only
            res_sym = client.get_product("---!@#---")
            assert res_sym.status == OFFStatus.INVALID_BARCODE

            # Never touched the network
            assert mock_get.call_count == 0

    def test_missing_and_null_nutrition_values_remain_none(self):
        client = OpenFoodFactsClient()
        payload = {
            "status": 1,
            "product": {
                "product_name": "Zero-Disclosure Food",
                "brands": "MysteryBrand",
                "nutriments": {},
            },
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = payload

        with patch("httpx.Client.get", return_value=mock_resp):
            res = client.get_product("123456789")

        assert res.is_success
        n = res.product.nutrition
        assert n.calories is None
        assert n.fat is None
        assert n.saturated_fat is None
        assert n.trans_fat is None
        assert n.carbs is None
        assert n.sugar is None
        assert n.protein is None
        assert n.fiber is None
        assert n.salt is None
        assert n.sodium is None

    def test_nutrition_normalization_salt_sodium_and_energy_conversions(self):
        # 1. sodium present -> salt calculated as sodium * 2.5
        # 2. energy_100g in kJ -> kcal calculated as kJ / 4.184
        payload = {
            "status": 1,
            "product": {
                "product_name": "Energy Cracker",
                "nutriments": {
                    "energy_100g": 1673.6,  # 1673.6 kJ ≈ 400.0 kcal
                    "sodium_100g": 0.4,     # 0.4g sodium * 2.5 = 1.0g salt
                },
            },
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = payload

        client = OpenFoodFactsClient()
        with patch("httpx.Client.get", return_value=mock_resp):
            res = client.get_product("8901234567800")

        assert res.is_success
        assert res.product.nutrition.calories == 400.0
        assert res.product.nutrition.sodium == 0.4
        assert res.product.nutrition.salt == 1.0

    def test_all_saturated_and_trans_fat_variations(self):
        # Variation 1: hyphenated
        v1 = {"product_name": "P1", "nutriments": {"saturated-fat_100g": 5.0, "trans-fat_100g": 0.2}}
        c1 = normalize_off_to_canonical("1001", v1)
        assert c1.nutrition.saturated_fat == 5.0
        assert c1.nutrition.trans_fat == 0.2

        # Variation 2: underscored
        v2 = {"product_name": "P2", "nutriments": {"saturated_fat_100g": 4.0, "trans_fat_100g": 0.1}}
        c2 = normalize_off_to_canonical("1002", v2)
        assert c2.nutrition.saturated_fat == 4.0
        assert c2.nutrition.trans_fat == 0.1

        # Variation 3: short keys
        v3 = {"product_name": "P3", "nutriments": {"saturated-fat": 3.0, "trans-fat": 0.05}}
        c3 = normalize_off_to_canonical("1003", v3)
        assert c3.nutrition.saturated_fat == 3.0
        assert c3.nutrition.trans_fat == 0.05

    def test_malformed_json_fallback(self):
        client = OpenFoodFactsClient(max_retries=0)
        mock_v3 = MagicMock(status_code=200)
        mock_v3.json.side_effect = json.JSONDecodeError("Bad JSON", "doc", 0)

        mock_v2 = MagicMock(status_code=200)
        mock_v2.json.return_value = MOCK_V2_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", side_effect=[mock_v3, mock_v2]):
            res = client.get_product("8901058851304")

        assert res.is_success
        assert res.endpoint_used == "v2"

    def test_missing_product_name_is_malformed(self):
        client = OpenFoodFactsClient()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"status": 1, "product": {"nutriments": {"fat": 10}}}

        with patch("httpx.Client.get", return_value=mock_resp):
            res = client.get_product("8901058851304")

        assert res.status == OFFStatus.MALFORMED_RESPONSE
        assert res.product is None


# ==============================================================================
# 2. RATE LIMITER ARCHITECTURE TESTS
# ==============================================================================

class TestRateLimiter:
    """Verifies process-local allocations, token consumption on retries, Redis Lua integration, and budget ceilings."""

    def test_process_local_worker_budget_calculation(self):
        # Defaults: WEB_CONCURRENCY=2, RENDER_INSTANCE_COUNT=1 (2 workers)
        limiter = OFFRateLimiter(web_concurrency=2, render_instance_count=1)
        assert limiter.worker_product_budget == 5  # floor(10 / 2)
        assert limiter.worker_search_budget == 2   # floor(4 / 2)

        # 3 workers: floor(10 / 3) = 3, floor(4 / 3) = 1
        limiter3 = OFFRateLimiter(web_concurrency=3, render_instance_count=1)
        assert limiter3.worker_product_budget == 3
        assert limiter3.worker_search_budget == 1

    def test_process_local_fail_closed_on_zero_budget(self):
        # Large replica/worker count causing worker budget to floor to 0
        limiter = OFFRateLimiter(web_concurrency=10, render_instance_count=2)
        assert limiter.worker_product_budget == 0  # floor(10 / 20) = 0
        assert limiter.worker_search_budget == 0   # floor(4 / 20) = 0

        # Must fail closed immediately
        assert limiter.acquire("product") is False
        assert limiter.acquire("search") is False

    def test_product_and_search_budget_separation(self):
        limiter = OFFRateLimiter(web_concurrency=1, render_instance_count=1, global_product_limit=2, global_search_limit=1)

        # Product: budget 2
        assert limiter.acquire("product") is True
        assert limiter.acquire("product") is True
        assert limiter.acquire("product") is False  # Exhausted

        # Search: budget 1 (independent from product)
        assert limiter.acquire("search") is True
        assert limiter.acquire("search") is False  # Exhausted

    def test_token_consumption_on_each_retry(self):
        # Budget of 3 tokens
        limiter = OFFRateLimiter(web_concurrency=1, render_instance_count=1, global_product_limit=3)
        client = OpenFoodFactsClient(rate_limiter=limiter, max_retries=2)

        # Mock 502 returning consistently
        mock_502 = MagicMock(status_code=502)
        with patch("httpx.Client.get", return_value=mock_502) as mock_get:
            res = client.get_product("8901234567890")

        # 3 attempts made (initial + retry 1 + retry 2) -> 3 tokens consumed
        assert mock_get.call_count == 3
        assert res.attempts == 3

        # Rate limiter must now be completely exhausted
        assert limiter.acquire("product") is False

    def test_token_consumption_on_v2_fallback(self):
        # Budget of 2 tokens
        limiter = OFFRateLimiter(web_concurrency=1, render_instance_count=1, global_product_limit=2)
        client = OpenFoodFactsClient(rate_limiter=limiter, max_retries=0)  # 0 retries on v3

        mock_503 = MagicMock(status_code=503)
        mock_v2 = MagicMock(status_code=200)
        mock_v2.json.return_value = MOCK_V2_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", side_effect=[mock_503, mock_v2]) as mock_get:
            res = client.get_product("8901234567890")

        # 1 token for v3, 1 token for v2 = 2 tokens consumed
        assert mock_get.call_count == 2
        assert res.is_success
        assert res.endpoint_used == "v2"
        # Limiter exhausted
        assert limiter.acquire("product") is False

    def test_exhausted_budget_denial(self):
        limiter = OFFRateLimiter(web_concurrency=1, render_instance_count=1, global_product_limit=1)
        assert limiter.acquire("product") is True
        assert limiter.acquire("product") is False

        client = OpenFoodFactsClient(rate_limiter=limiter)
        res = client.get_product("8901234567890")
        assert res.is_rate_limited
        assert res.status == OFFStatus.RATE_LIMITED
        assert "Rate limit budget exhausted" in str(res.error)

    def test_concurrent_request_safety(self):
        # 10 tokens available
        limiter = OFFRateLimiter(web_concurrency=1, render_instance_count=1, global_product_limit=10)
        successes = []

        def worker():
            for _ in range(3):
                if limiter.acquire("product"):
                    successes.append(1)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 10 tokens must have been granted, never exceeding ceiling
        assert sum(successes) == 10

    def test_redis_limiter_lua_atomic_execution(self):
        mock_redis = MagicMock()
        # Simulate Lua return: 1 (acquired) then 0 (exhausted)
        mock_redis.eval.side_effect = [1, 0]
        mock_redis.get.return_value = None

        limiter = OFFRateLimiter(redis_client=mock_redis, global_product_limit=10)
        assert limiter.is_redis_coordinated

        assert limiter.acquire("product") is True
        assert limiter.acquire("product") is False
        assert mock_redis.eval.call_count == 2

    def test_redis_cooldown_coordination(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1"  # Key exists in Redis

        limiter = OFFRateLimiter(redis_client=mock_redis)
        assert limiter.is_in_cooldown() is True
        assert limiter.acquire("product") is False


# ==============================================================================
# 3. RETRY AND HTTP 429 COOLDOWN TESTS
# ==============================================================================

class TestRetryAndCooldown:
    """Verifies backoff, status code retry policies, 429 cooldown parsing, and short-circuiting."""

    def test_retries_on_502_503_504(self):
        client = OpenFoodFactsClient(max_retries=2)
        mock_502 = MagicMock(status_code=502)
        mock_200 = MagicMock(status_code=200)
        mock_200.json.return_value = MOCK_V3_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", side_effect=[mock_502, mock_502, mock_200]) as mock_get:
            res = client.get_product("8901058851304")

        assert res.is_success
        assert mock_get.call_count == 3
        assert res.attempts == 3

    def test_retries_on_timeout_and_network_error(self):
        client = OpenFoodFactsClient(max_retries=2)
        mock_200 = MagicMock(status_code=200)
        mock_200.json.return_value = MOCK_V3_PRODUCT_PAYLOAD

        with patch(
            "httpx.Client.get",
            side_effect=[httpx.ConnectTimeout("Connect timeout"), httpx.NetworkError("DNS fail"), mock_200],
        ) as mock_get:
            res = client.get_product("8901058851304")

        assert res.is_success
        assert mock_get.call_count == 3

    def test_no_retries_on_400_401_403(self):
        for code in (400, 401, 403):
            client = OpenFoodFactsClient(max_retries=2)
            mock_err = MagicMock(status_code=code)
            with patch("httpx.Client.get", return_value=mock_err) as mock_get:
                res = client.get_product("8901058851304")
            assert res.status == OFFStatus.SERVER_ERROR
            assert res.http_status == code
            assert mock_get.call_count == 1  # Fast-fail without retries

    def test_http_429_integer_retry_after(self):
        client = OpenFoodFactsClient()
        mock_429 = MagicMock(status_code=429)
        mock_429.headers = {"Retry-After": "120"}

        with patch("httpx.Client.get", return_value=mock_429):
            res = client.get_product("8901058851304")

        assert res.is_rate_limited
        assert res.status == OFFStatus.RATE_LIMITED
        assert client.rate_limiter.is_in_cooldown()
        assert client.rate_limiter.get_cooldown_remaining() >= 60.0

    def test_http_429_http_date_retry_after(self):
        client = OpenFoodFactsClient()
        mock_429 = MagicMock(status_code=429)
        future_dt = datetime.now(timezone.utc) + timedelta(seconds=90)
        http_date_str = email.utils.format_datetime(future_dt)
        mock_429.headers = {"Retry-After": http_date_str}

        with patch("httpx.Client.get", return_value=mock_429):
            res = client.get_product("8901058851304")

        assert res.is_rate_limited
        assert client.rate_limiter.is_in_cooldown()

    def test_http_429_default_60s_cooldown(self):
        client = OpenFoodFactsClient()
        mock_429 = MagicMock(status_code=429)
        mock_429.headers = {}  # Missing header

        with patch("httpx.Client.get", return_value=mock_429):
            res = client.get_product("8901058851304")

        assert res.is_rate_limited
        assert client.rate_limiter.is_in_cooldown()
        assert client.rate_limiter.get_cooldown_remaining() >= 59.0

    def test_retry_exhaustion_returns_error(self):
        client = OpenFoodFactsClient(max_retries=1)
        mock_502 = MagicMock(status_code=502)

        with patch("httpx.Client.get", return_value=mock_502) as mock_get:
            res = client.get_product("8901058851304")

        assert res.is_success is False
        assert res.status in (OFFStatus.SERVER_ERROR, OFFStatus.NETWORK_UNAVAILABLE)
        # 2 attempts on v3 (initial + retry 1) + 2 attempts on v2 (initial + retry 1) = 4 attempts total
        assert mock_get.call_count == 4
        assert res.attempts == 4

    def test_search_functionality_and_caching(self):
        client = OpenFoodFactsClient()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "count": 1,
            "products": [
                {
                    "code": "8901058851304",
                    "product_name": "Maggi 2-Minute Noodles",
                    "brands": "Nestle",
                    "nutriments": {"energy-kcal_100g": 427.0},
                }
            ],
        }

        with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
            res1 = client.search("maggi", limit=5)
            assert res1.status == OFFStatus.SUCCESS
            assert len(res1.products) == 1
            assert res1.products[0]["product_name"] == "Maggi 2-Minute Noodles"
            assert mock_get.call_count == 1

            # Second call should hit cache without network request
            res2 = client.search("maggi", limit=5)
            assert res2.status == OFFStatus.SUCCESS
            assert res2.from_cache is True
            assert mock_get.call_count == 1  # Unchanged

    def test_cooldown_short_circuit_blocks_remote_calls(self):
        client = OpenFoodFactsClient()
        client.rate_limiter.trigger_cooldown(60.0)

        with patch("httpx.Client.get") as mock_get:
            res = client.get_product("8901058851304")
            res_search = client.search("noodles")

        assert res.is_rate_limited
        assert res_search.status == OFFStatus.RATE_LIMITED
        # Completely short-circuited; zero remote HTTP requests transmitted
        assert mock_get.call_count == 0


# ==============================================================================
# 4. BOUNDED LRU CACHING TESTS
# ==============================================================================

class TestLRUCache:
    """Verifies hit/miss, expiration, eviction, key isolation, mutation safety, and error exclusion."""

    def test_cache_hit_and_miss(self):
        cache = OFFLRUCache(max_size=10, default_ttl=60.0)
        assert cache.get("p:1") is None

        cache.set("p:1", {"name": "Test"})
        hit = cache.get("p:1")
        assert hit == {"name": "Test"}

    def test_cache_ttl_expiration(self):
        cache = OFFLRUCache(max_size=10, default_ttl=0.05)  # 50ms TTL
        cache.set("p:expire", {"name": "Expiring"})
        assert cache.get("p:expire") is not None

        time.sleep(0.06)
        assert cache.get("p:expire") is None

    def test_cache_lru_eviction(self):
        cache = OFFLRUCache(max_size=2)
        cache.set("k1", "val1")
        cache.set("k2", "val2")
        assert len(cache) == 2

        # Access k1 to make k2 least recently used
        assert cache.get("k1") == "val1"

        # Add k3 -> k2 should be evicted
        cache.set("k3", "val3")
        assert len(cache) == 2
        assert cache.get("k2") is None
        assert cache.get("k1") == "val1"
        assert cache.get("k3") == "val3"

    def test_cache_key_isolation_product_and_search(self):
        cache = OFFLRUCache()
        cache.set("product:12345", {"type": "prod"})
        cache.set("search:12345:10", {"type": "search"})

        assert cache.get("product:12345") == {"type": "prod"}
        assert cache.get("search:12345:10") == {"type": "search"}

    def test_failed_responses_never_cached(self):
        cache = OFFLRUCache()
        client = OpenFoodFactsClient(cache=cache)

        # 404
        mock_404 = MagicMock(status_code=404)
        with patch("httpx.Client.get", return_value=mock_404):
            client.get_product("404040404")
        assert len(cache) == 0

        # 500
        mock_500 = MagicMock(status_code=500)
        with patch("httpx.Client.get", return_value=mock_500):
            client.get_product("500500500")
        assert len(cache) == 0

    def test_defensive_copies_mutation_safety(self):
        cache = OFFLRUCache()
        sample = CanonicalProduct(
            canonical_id="123",
            barcode="123",
            product_name="Original Name",
            nutrition=Nutrition100g(calories=100.0),
        )
        cache.set("p:123", sample)

        retrieved = cache.get("p:123")
        assert retrieved.product_name == "Original Name"

        # Caller mutates returned object
        retrieved.product_name = "Mutated Name"
        retrieved.nutrition.calories = 999.0

        # Cached object must remain pristine
        second_retrieval = cache.get("p:123")
        assert second_retrieval.product_name == "Original Name"
        assert second_retrieval.nutrition.calories == 100.0


# ==============================================================================
# 5. LEGACY COMPATIBILITY WRAPPER TESTS
# ==============================================================================

class TestLegacyCompatibilityWrapper:
    """Verifies existing openfoodfacts_service.py signatures and caller compatibility."""

    def test_fetch_product_by_barcode_returns_legacy_dict(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = MOCK_V3_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", return_value=mock_resp):
            res = fetch_product_by_barcode("8901058851304")

        assert isinstance(res, dict)
        assert res["barcode"] == "8901058851304"
        assert res["product_name"] == "Maggi 2-Minute Noodles"
        assert res["brand"] == "Nestle"
        assert res["calories"] == 427.0
        assert res["saturated_fat"] == 6.8
        assert res["trans_fat"] == 0.1
        assert res["salt"] == 2.3

    def test_fetch_product_by_barcode_v2_forces_v2(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = MOCK_V2_PRODUCT_PAYLOAD

        with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
            res = fetch_product_by_barcode_v2("8901058851304")

        assert isinstance(res, dict)
        assert res["product_name"] == "Maggi 2-Minute Noodles (v2)"
        assert "api/v2" in mock_get.call_args[0][0]

    @pytest.mark.anyio
    async def test_async_wrapper_methods(self):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = MOCK_V3_PRODUCT_PAYLOAD

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            res = await fetch_product_by_barcode_async("8901058851304")

        assert isinstance(res, dict)
        assert res["barcode"] == "8901058851304"

    def test_legacy_helpers_to_float_and_normalize_dict(self):
        assert _to_float("12.5") == 12.5
        assert _to_float(None) is None
        assert _to_float("") is None
        assert _to_float("invalid") is None

        raw = {
            "product_name": "Sample",
            "nutriments": {"energy-kcal_100g": 150, "fat_100g": 5},
        }
        norm = _normalize_product_dict("777", raw)
        assert norm["barcode"] == "777"
        assert norm["product_name"] == "Sample"
        assert norm["calories"] == 150.0
        assert norm["fat"] == 5.0
        assert norm["sugar"] is None
