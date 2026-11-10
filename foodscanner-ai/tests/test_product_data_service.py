from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from database.canonical_adapter import (
    CONFIDENCE_RANK,
    PROVENANCE_RANK,
    db_product_to_canonical,
    merge_canonical_products,
)
from database.models import Base, Nutrition, Product, ProductBarcodeAlias
from database.orm import create_database_engine
from schemas.product import (
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
)
from services.db_service import (
    add_barcode_alias,
    get_canonical_product_by_barcode,
    upsert_canonical_product,
)
from services.off_client import (
    OFFResult,
    OFFStatus,
    OpenFoodFactsClient,
)
from services.product_data_service import (
    ProductDataService,
    get_default_product_data_service,
)

BASE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BASE_DIR / "alembic.ini"


def _get_alembic_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def isolated_db_session(tmp_path):
    """Provides an isolated SQLite database migrated to latest schema (revision 004)."""
    db_path = tmp_path / "test_pds_isolated.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    cfg = _get_alembic_config(db_url)

    # Run complete Alembic migrations to head (004)
    command.upgrade(cfg, "head")

    engine = create_database_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ==============================================================================
# 1. QUALITY-AWARE FIELD-LEVEL MERGE TESTS
# ==============================================================================

class TestQualityAwareFieldMerge:
    """Verifies centralized, idempotent, quality-aware field merge logic."""

    def test_admin_verified_data_is_immutable_to_external_updates(self):
        """ADMIN_VERIFIED fields are never overwritten by unverified or API data."""
        now = datetime.now(timezone.utc)
        existing = CanonicalProduct(
            canonical_id="8901058851304",
            barcode="8901058851304",
            product_name="Maggi Admin Noodles",
            brand="Nestle Verified",
            nutrition=Nutrition100g(calories=420.0, sugar=2.0, fat=15.0, salt=2.1),
            primary_provenance=DataProvenance.ADMIN_VERIFIED,
            field_provenance={
                "product_name": FieldProvenance(source=DataProvenance.ADMIN_VERIFIED, confidence=DataConfidence.HIGH, timestamp=now),
                "nutrition": FieldProvenance(source=DataProvenance.ADMIN_VERIFIED, confidence=DataConfidence.HIGH, timestamp=now),
            },
            last_fetched_at=now - timedelta(days=60),
        )

        incoming = CanonicalProduct(
            canonical_id="8901058851304",
            barcode="8901058851304",
            product_name="Maggi Cheap Overwrite Name",
            brand="Nestle API",
            nutrition=Nutrition100g(calories=999.0, sugar=50.0, fat=40.0, salt=5.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            field_provenance={
                "product_name": FieldProvenance(source=DataProvenance.OFF_API_V3, confidence=DataConfidence.MEDIUM, timestamp=now),
                "nutrition": FieldProvenance(source=DataProvenance.OFF_API_V3, confidence=DataConfidence.MEDIUM, timestamp=now),
            },
            last_fetched_at=now,
        )

        merged = merge_canonical_products(existing, incoming)

        # ADMIN_VERIFIED name and nutrition must remain completely unchanged
        assert merged.product_name == "Maggi Admin Noodles"
        assert merged.nutrition.calories == 420.0
        assert merged.nutrition.sugar == 2.0
        assert merged.primary_provenance == DataProvenance.ADMIN_VERIFIED

    def test_missing_values_never_overwrite_valid_existing_data(self):
        """Incoming None must NEVER overwrite valid existing disclosures."""
        existing = CanonicalProduct(
            canonical_id="111",
            barcode="111",
            product_name="Rich Cookie",
            nutrition=Nutrition100g(calories=500.0, sugar=25.0, fat=20.0, salt=1.2, protein=8.0, fiber=4.0),
        )

        incoming = CanonicalProduct(
            canonical_id="111",
            barcode="111",
            product_name="Rich Cookie",
            nutrition=Nutrition100g(calories=500.0, sugar=None, fat=None, salt=None, protein=None, fiber=None),
            primary_provenance=DataProvenance.OFF_API_V3,
        )

        merged = merge_canonical_products(existing, incoming)

        assert merged.nutrition.calories == 500.0
        assert merged.nutrition.sugar == 25.0
        assert merged.nutrition.fat == 20.0
        assert merged.nutrition.salt == 1.2
        assert merged.nutrition.protein == 8.0
        assert merged.nutrition.fiber == 4.0

    def test_explicit_zero_values_preserved_and_missing_values_remain_none(self):
        """Explicit 0.0 is a valid value, while missing values strictly remain None (never 0.0)."""
        existing = CanonicalProduct(
            canonical_id="222",
            barcode="222",
            product_name="Zero Calorie Soda",
            nutrition=Nutrition100g(calories=None, fat=None, trans_fat=None),
        )

        incoming = CanonicalProduct(
            canonical_id="222",
            barcode="222",
            product_name="Zero Calorie Soda",
            nutrition=Nutrition100g(calories=0.0, fat=0.0, trans_fat=None),
        )

        merged = merge_canonical_products(existing, incoming)

        assert merged.nutrition.calories == 0.0
        assert merged.nutrition.fat == 0.0
        assert merged.nutrition.trans_fat is None  # Remains strictly None!

    def test_higher_confidence_and_provenance_precedence(self):
        """Disclosures with higher confidence or higher provenance take precedence."""
        existing = CanonicalProduct(
            canonical_id="333",
            barcode="333",
            product_name="Old Name",
            brand="Old Brand",
            nutrition=Nutrition100g(calories=100.0),
            primary_provenance=DataProvenance.LOCAL_DB,
            field_provenance={
                "brand": FieldProvenance(source=DataProvenance.LOCAL_DB, confidence=DataConfidence.LOW),
            },
        )

        incoming = CanonicalProduct(
            canonical_id="333",
            barcode="333",
            product_name="Updated Name",
            brand="Official High Conf Brand",
            nutrition=Nutrition100g(calories=110.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            field_provenance={
                "brand": FieldProvenance(source=DataProvenance.OFF_API_V3, confidence=DataConfidence.HIGH),
            },
        )

        merged = merge_canonical_products(existing, incoming)
        assert merged.brand == "Official High Conf Brand"

    def test_salt_and_sodium_complementary_merge(self):
        """If existing has salt and incoming has sodium, both are preserved without data loss."""
        existing = CanonicalProduct(
            canonical_id="444",
            barcode="444",
            product_name="Salt Snack",
            nutrition=Nutrition100g(calories=200.0, salt=2.5, sodium=None),
        )

        incoming = CanonicalProduct(
            canonical_id="444",
            barcode="444",
            product_name="Salt Snack",
            nutrition=Nutrition100g(calories=200.0, salt=None, sodium=1.0),
        )

        merged = merge_canonical_products(existing, incoming)
        assert merged.nutrition.salt == 2.5
        assert merged.nutrition.sodium == 1.0

    def test_merge_idempotency(self):
        """merge(A, B) == merge(merge(A, B), B)."""
        prod_a = CanonicalProduct(
            canonical_id="555",
            barcode="555",
            product_name="Idempotent Item",
            nutrition=Nutrition100g(calories=150.0, sugar=10.0),
        )
        prod_b = CanonicalProduct(
            canonical_id="555",
            barcode="555",
            product_name="Idempotent Item (Updated)",
            nutrition=Nutrition100g(calories=155.0, fat=5.0),
            primary_provenance=DataProvenance.OFF_API_V3,
        )

        merged1 = merge_canonical_products(prod_a, prod_b)
        merged2 = merge_canonical_products(merged1, prod_b)

        assert merged1.product_name == merged2.product_name
        assert merged1.nutrition.calories == merged2.nutrition.calories
        assert merged1.nutrition.sugar == merged2.nutrition.sugar
        assert merged1.nutrition.fat == merged2.nutrition.fat


# ==============================================================================
# 2. PRODUCT IDENTITY & VARIANT PROTECTION TESTS
# ==============================================================================

class TestProductIdentityAndVariantProtection:
    """Verifies that distinct barcodes/variants are strictly protected from erroneous merges."""

    def test_refuse_merge_different_barcodes_even_with_identical_names(self):
        """Products with similar/identical names but different barcodes MUST NOT merge."""
        prod1 = CanonicalProduct(
            canonical_id="8901058851304",
            barcode="8901058851304",
            product_name="Maggi 2-Minute Masala Noodles 70g",
            nutrition=Nutrition100g(calories=427.0),
        )
        prod2 = CanonicalProduct(
            canonical_id="8901058851311",
            barcode="8901058851311",
            product_name="Maggi 2-Minute Masala Noodles 140g",
            nutrition=Nutrition100g(calories=430.0),
        )

        with pytest.raises(ValueError, match="Cannot merge products with different identities"):
            merge_canonical_products(prod1, prod2)

    def test_merge_allowed_for_registered_aliases(self):
        """Merging is permitted when the incoming barcode is a recognized alias."""
        primary = CanonicalProduct(
            canonical_id="8901058851304",
            barcode="8901058851304",
            alternate_barcodes=["8901058851399"],
            product_name="Maggi 2-Minute Noodles",
            nutrition=Nutrition100g(calories=427.0),
        )
        alias_product = CanonicalProduct(
            canonical_id="8901058851304",
            barcode="8901058851399",
            product_name="Maggi 2-Minute Noodles Promo Pack",
            nutrition=Nutrition100g(calories=427.0, fiber=3.5),
        )

        merged = merge_canonical_products(primary, alias_product)
        assert merged.barcode == "8901058851304"
        assert "8901058851399" in merged.alternate_barcodes
        assert merged.nutrition.fiber == 3.5


# ==============================================================================
# 3. DATABASE CACHE FRESHNESS & REFRESH POLICY TESTS
# ==============================================================================

class TestDatabaseCacheAndFreshnessPolicy:
    """Verifies cache hits, staleness detection, and external request avoidance."""

    def test_fresh_database_record_avoids_external_request(self, isolated_db_session):
        """Fresh products (< 30 days old) in DB return immediately with ZERO API calls."""
        now = datetime.now(timezone.utc)
        fresh_product = CanonicalProduct(
            canonical_id="8901234567890",
            barcode="8901234567890",
            product_name="Fresh DB Flakes",
            nutrition=Nutrition100g(calories=380.0, sugar=5.0, fat=1.5, salt=0.8),
            last_fetched_at=now - timedelta(days=5),
        )
        upsert_canonical_product(isolated_db_session, fresh_product)
        isolated_db_session.commit()

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        service = ProductDataService(off_client=mock_off_client, cache_ttl_days=30)

        result = service.get_product_by_barcode(isolated_db_session, "8901234567890")

        assert result is not None
        assert result.product_name == "Fresh DB Flakes"
        # Zero external requests issued!
        assert mock_off_client.get_product.call_count == 0

    def test_missing_timestamp_is_never_treated_as_fresh(self, isolated_db_session):
        """Products without a last_fetched_at timestamp MUST trigger an external refresh check."""
        product_no_ts = CanonicalProduct(
            canonical_id="8909876543210",
            barcode="8909876543210",
            product_name="Undated Historical Product",
            nutrition=Nutrition100g(calories=200.0),
            last_fetched_at=None,
        )
        upsert_canonical_product(isolated_db_session, product_no_ts)
        isolated_db_session.commit()

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        now = datetime.now(timezone.utc)
        refreshed_canonical = CanonicalProduct(
            canonical_id="8909876543210",
            barcode="8909876543210",
            product_name="Undated Historical Product (Refreshed)",
            nutrition=Nutrition100g(calories=205.0, sugar=3.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.SUCCESS,
            barcode="8909876543210",
            product=refreshed_canonical,
            http_status=200,
        )

        service = ProductDataService(off_client=mock_off_client, cache_ttl_days=30)

        assert service.is_fresh(product_no_ts) is False

        result = service.get_product_by_barcode(isolated_db_session, "8909876543210")
        assert result is not None
        assert mock_off_client.get_product.call_count == 1
        assert result.nutrition.sugar == 3.0

    def test_stale_record_triggers_refresh_and_persists_merged_data(self, isolated_db_session):
        """Stale records (> 30 days) fetch newer data and merge quality-aware."""
        now = datetime.now(timezone.utc)
        stale_product = CanonicalProduct(
            canonical_id="8901112223334",
            barcode="8901112223334",
            product_name="Old Stale Biscuits",
            nutrition=Nutrition100g(calories=450.0, sugar=20.0),
            last_fetched_at=now - timedelta(days=45),
        )
        upsert_canonical_product(isolated_db_session, stale_product)
        isolated_db_session.commit()

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        refreshed = CanonicalProduct(
            canonical_id="8901112223334",
            barcode="8901112223334",
            product_name="Old Stale Biscuits (Fresh)",
            nutrition=Nutrition100g(calories=450.0, sugar=18.0, fat=12.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.SUCCESS,
            barcode="8901112223334",
            product=refreshed,
            http_status=200,
        )

        service = ProductDataService(off_client=mock_off_client, cache_ttl_days=30)
        result = service.get_product_by_barcode(isolated_db_session, "8901112223334")

        assert result is not None
        assert mock_off_client.get_product.call_count == 1
        assert result.nutrition.fat == 12.0

    def test_cache_ttl_validation_safe_fallbacks(self, monkeypatch):
        """Verify TTL environment variable parsing and safety bounds."""
        # 1. Missing environment variables -> default 30
        monkeypatch.delenv("OFF_CACHE_TTL_DAYS", raising=False)
        monkeypatch.delenv("CACHE_TTL_DAYS", raising=False)
        svc_default = ProductDataService()
        assert svc_default.cache_ttl_days == 30

        # 2. Valid positive integer -> parsed
        monkeypatch.setenv("OFF_CACHE_TTL_DAYS", "45")
        svc_valid = ProductDataService()
        assert svc_valid.cache_ttl_days == 45

        # 3. Valid CACHE_TTL_DAYS fallback
        monkeypatch.delenv("OFF_CACHE_TTL_DAYS", raising=False)
        monkeypatch.setenv("CACHE_TTL_DAYS", "15")
        svc_fallback = ProductDataService()
        assert svc_fallback.cache_ttl_days == 15

        # 4. Zero value -> safe default 30
        monkeypatch.setenv("OFF_CACHE_TTL_DAYS", "0")
        svc_zero = ProductDataService()
        assert svc_zero.cache_ttl_days == 30

        # 5. Negative value -> safe default 30
        monkeypatch.setenv("OFF_CACHE_TTL_DAYS", "-10")
        svc_negative = ProductDataService()
        assert svc_negative.cache_ttl_days == 30

        # 6. Invalid non-numeric string -> safe default 30
        monkeypatch.setenv("OFF_CACHE_TTL_DAYS", "not_a_number")
        svc_invalid = ProductDataService()
        assert svc_invalid.cache_ttl_days == 30

        # 7. Non-positive constructor argument -> safe default 30
        monkeypatch.delenv("OFF_CACHE_TTL_DAYS", raising=False)
        monkeypatch.delenv("CACHE_TTL_DAYS", raising=False)
        svc_arg_zero = ProductDataService(cache_ttl_days=0)
        assert svc_arg_zero.cache_ttl_days == 30
        svc_arg_neg = ProductDataService(cache_ttl_days=-5)
        assert svc_arg_neg.cache_ttl_days == 30


# ==============================================================================
# 4. OFFLINE FALLBACK & FAILURE RESILIENCE TESTS
# ==============================================================================

class TestOfflineAndFailureHandling:
    """Verifies that API errors (404, 429, 5xx, timeouts) degrade safely without failing users."""

    def test_stale_db_fallback_when_off_returns_429(self, isolated_db_session):
        """When OFF is rate-limited (HTTP 429), existing stale DB data is returned."""
        now = datetime.now(timezone.utc)
        stale_product = CanonicalProduct(
            canonical_id="8905556667778",
            barcode="8905556667778",
            product_name="Resilient Butter",
            nutrition=Nutrition100g(calories=720.0, fat=80.0),
            last_fetched_at=now - timedelta(days=60),
        )
        upsert_canonical_product(isolated_db_session, stale_product)
        isolated_db_session.commit()

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.RATE_LIMITED,
            barcode="8905556667778",
            http_status=429,
            error="Rate limit exceeded",
        )

        service = ProductDataService(off_client=mock_off_client, cache_ttl_days=30)
        res = service.get_product_by_barcode(isolated_db_session, "8905556667778")

        assert res is not None
        assert res.product_name == "Resilient Butter"
        assert res.nutrition.fat == 80.0

    def test_stale_db_fallback_when_off_times_out_or_503(self, isolated_db_session):
        """When OFF experiences a 503 or network timeout, existing DB data is returned."""
        now = datetime.now(timezone.utc)
        stale_product = CanonicalProduct(
            canonical_id="8904443332221",
            barcode="8904443332221",
            product_name="Timeout Safe Tea",
            nutrition=Nutrition100g(calories=100.0),
            last_fetched_at=now - timedelta(days=40),
        )
        upsert_canonical_product(isolated_db_session, stale_product)
        isolated_db_session.commit()

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.NETWORK_UNAVAILABLE,
            barcode="8904443332221",
            error="ConnectTimeout",
        )

        service = ProductDataService(off_client=mock_off_client, cache_ttl_days=30)
        res = service.get_product_by_barcode(isolated_db_session, "8904443332221")

        assert res is not None
        assert res.product_name == "Timeout Safe Tea"

    def test_database_write_failure_gracefully_returns_in_memory_product(self):
        """If database write fails, in-memory CanonicalProduct is still returned to the caller."""
        now = datetime.now(timezone.utc)
        fetched_product = CanonicalProduct(
            canonical_id="8901239999999",
            barcode="8901239999999",
            product_name="Disk Error Safe Oats",
            nutrition=Nutrition100g(calories=370.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.SUCCESS,
            barcode="8901239999999",
            product=fetched_product,
            http_status=200,
        )

        # Mock a broken DB session whose commit raises an exception
        mock_broken_db = MagicMock(spec=Session)
        mock_broken_db.execute.return_value.scalar_one_or_none.return_value = None
        mock_broken_db.commit.side_effect = RuntimeError("Database disk full or locked")

        service = ProductDataService(off_client=mock_off_client)
        res = service.get_product_by_barcode(mock_broken_db, "8901239999999")

        assert res is not None
        assert res.product_name == "Disk Error Safe Oats"

    def test_csv_fallback_for_openfoodfacts_cleaned(self, isolated_db_session):
        """When not in DB and OFF returns 404, fallback to cleaned OpenFoodFacts CSV by barcode."""
        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.NOT_FOUND,
            barcode="8901058851304",
            http_status=404,
        )

        service = ProductDataService(off_client=mock_off_client)
        res = service.get_product_by_barcode(
            isolated_db_session,
            "8901058851304",
        )

        assert res is not None
        assert "Maggi" in res.product_name
        assert res.primary_provenance == DataProvenance.OFF_CSV

        # Verify it was cached to the database
        db_check = get_canonical_product_by_barcode(isolated_db_session, "8901058851304")
        assert db_check is not None
        assert "Maggi" in db_check.product_name

    def test_csv_fallback_for_indian_packaged_foods_by_hint(self, isolated_db_session):
        """When not in DB and OFF returns 404 and barcode not in cleaned CSV, fallback to Indian packaged foods CSV by hint."""
        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        # Barcode not present in cleaned CSV
        non_existent_code = "9998887776665"
        mock_off_client.get_product.return_value = OFFResult(
            status=OFFStatus.NOT_FOUND,
            barcode=non_existent_code,
            http_status=404,
        )

        service = ProductDataService(off_client=mock_off_client)
        res = service.get_product_by_barcode(
            isolated_db_session,
            non_existent_code,
            product_name_hint="Amul Butter",
        )

        assert res is not None
        assert "Amul" in res.product_name
        assert res.primary_provenance == DataProvenance.INDIAN_CSV

        # Verify it was cached to the database
        db_check = get_canonical_product_by_barcode(isolated_db_session, non_existent_code)
        assert db_check is not None
        assert db_check.barcode == non_existent_code


# ==============================================================================
# 5. SINGLE-FLIGHT CONCURRENCY TESTS
# ==============================================================================

class TestSingleFlightConcurrency:
    """Verifies that concurrent requests for the same uncached barcode share a single flight."""

    def test_single_flight_deduplication(self, isolated_db_session):
        """Five simultaneous threads for the same barcode execute only ONE outbound API call."""
        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        barcode = "8907778889990"
        now = datetime.now(timezone.utc)
        canonical = CanonicalProduct(
            canonical_id=barcode,
            barcode=barcode,
            product_name="Concurrent Energy Drink",
            nutrition=Nutrition100g(calories=45.0, sugar=11.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )

        def slow_fetch(b):
            import time
            time.sleep(0.1)  # Simulate network latency
            return OFFResult(
                status=OFFStatus.SUCCESS,
                barcode=barcode,
                product=canonical,
                http_status=200,
            )

        mock_off_client.get_product.side_effect = slow_fetch
        service = ProductDataService(off_client=mock_off_client)

        results = []

        def worker():
            res = service.get_product_by_barcode(isolated_db_session, barcode)
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        assert all(r is not None and r.product_name == "Concurrent Energy Drink" for r in results)
        # Crucial check: Despite 5 concurrent calls, only 1 external API fetch was issued!
        assert mock_off_client.get_product.call_count == 1
        assert len(service._in_flight) == 0

    def test_single_flight_memory_cleanup_after_completion(self, isolated_db_session):
        """Verify in-flight memory structures are completely empty after all concurrent requests finish."""
        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        barcode = "8901239991112"
        now = datetime.now(timezone.utc)
        canonical = CanonicalProduct(
            canonical_id=barcode,
            barcode=barcode,
            product_name="Memory Safe Juice",
            nutrition=Nutrition100g(calories=50.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )

        def slow_fetch(b):
            import time
            time.sleep(0.05)
            return OFFResult(
                status=OFFStatus.SUCCESS,
                barcode=barcode,
                product=canonical,
                http_status=200,
            )

        mock_off_client.get_product.side_effect = slow_fetch
        service = ProductDataService(off_client=mock_off_client)

        results = []

        def worker():
            res = service.get_product_by_barcode(isolated_db_session, barcode)
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 5 threads received the exact same result
        assert len(results) == 5
        assert all(r is not None and r.product_name == "Memory Safe Juice" for r in results)
        assert mock_off_client.get_product.call_count == 1

        # CRITICAL: _in_flight dictionary must be completely empty! Zero lingering memory.
        assert len(service._in_flight) == 0

    @pytest.mark.anyio
    async def test_async_single_flight_memory_cleanup(self, isolated_db_session):
        """Verify async in-flight memory structures are completely empty after concurrent async requests."""
        from unittest.mock import AsyncMock

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        barcode = "8901239993334"
        now = datetime.now(timezone.utc)
        canonical = CanonicalProduct(
            canonical_id=barcode,
            barcode=barcode,
            product_name="Async Memory Safe Tea",
            nutrition=Nutrition100g(calories=10.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )

        async def slow_fetch_async(b):
            await asyncio.sleep(0.05)
            return OFFResult(
                status=OFFStatus.SUCCESS,
                barcode=barcode,
                product=canonical,
                http_status=200,
            )

        mock_off_client.get_product_async = AsyncMock(side_effect=slow_fetch_async)
        service = ProductDataService(off_client=mock_off_client)

        # Launch 5 concurrent coroutines
        results = await asyncio.gather(
            service.get_product_by_barcode_async(isolated_db_session, barcode),
            service.get_product_by_barcode_async(isolated_db_session, barcode),
            service.get_product_by_barcode_async(isolated_db_session, barcode),
            service.get_product_by_barcode_async(isolated_db_session, barcode),
            service.get_product_by_barcode_async(isolated_db_session, barcode),
        )

        assert len(results) == 5
        assert all(r is not None and r.product_name == "Async Memory Safe Tea" for r in results)
        assert mock_off_client.get_product_async.call_count == 1

        # CRITICAL: _async_in_flight dictionary must be completely empty!
        assert len(service._async_in_flight) == 0


# ==============================================================================
# 6. ASYNC METHODS & ALIAS COLLISION GUARDS
# ==============================================================================

class TestAsyncAndAliasGuards:
    """Verifies async workflow and alias collision prevention."""

    @pytest.mark.anyio
    async def test_async_get_product_by_barcode(self, isolated_db_session):
        from unittest.mock import AsyncMock

        mock_off_client = MagicMock(spec=OpenFoodFactsClient)
        now = datetime.now(timezone.utc)
        barcode = "8901234560000"
        canonical = CanonicalProduct(
            canonical_id=barcode,
            barcode=barcode,
            product_name="Async Herbal Tea",
            nutrition=Nutrition100g(calories=0.0),
            primary_provenance=DataProvenance.OFF_API_V3,
            last_fetched_at=now,
        )

        mock_off_client.get_product_async = AsyncMock(
            return_value=OFFResult(
                status=OFFStatus.SUCCESS,
                barcode=barcode,
                product=canonical,
                http_status=200,
            )
        )

        service = ProductDataService(off_client=mock_off_client)
        res = await service.get_product_by_barcode_async(isolated_db_session, barcode)

        assert res is not None
        assert res.product_name == "Async Herbal Tea"
        assert res.nutrition.calories == 0.0

    def test_alias_guards_and_precedence(self, isolated_db_session):
        """Primary barcode lookup takes precedence over aliases, and collisions are rejected."""
        prod1 = CanonicalProduct(
            canonical_id="10001",
            barcode="10001",
            product_name="Main Product 1",
            nutrition=Nutrition100g(calories=100.0),
        )
        prod2 = CanonicalProduct(
            canonical_id="10002",
            barcode="10002",
            product_name="Main Product 2",
            nutrition=Nutrition100g(calories=200.0),
        )
        upsert_canonical_product(isolated_db_session, prod1)
        upsert_canonical_product(isolated_db_session, prod2)
        isolated_db_session.commit()

        p1_db = isolated_db_session.execute(select(Product).where(Product.barcode == "10001")).scalar_one()

        # 1. Alias cannot collide with an existing primary barcode
        with pytest.raises(ValueError, match="already exists as primary barcode"):
            add_barcode_alias(isolated_db_session, p1_db.id, "10002")

        # 2. Alias cannot be identical to target's primary barcode (no self-alias)
        with pytest.raises(ValueError, match="alias cannot be identical to target product"):
            add_barcode_alias(isolated_db_session, p1_db.id, "10001")

        # 3. Valid alias registration
        alias_entry = add_barcode_alias(isolated_db_session, p1_db.id, "10001-ALT")
        isolated_db_session.commit()
        assert alias_entry.alias_barcode == "10001-ALT"

        # 4. Lookup by alias resolves to the primary product
        resolved = get_canonical_product_by_barcode(isolated_db_session, "10001-ALT")
        assert resolved is not None
        assert resolved.barcode == "10001"
        assert resolved.product_name == "Main Product 1"

        # 5. Alias cannot be claimed by another product
        p2_db = isolated_db_session.execute(select(Product).where(Product.barcode == "10002")).scalar_one()
        with pytest.raises(ValueError, match="alias already belongs to product"):
            add_barcode_alias(isolated_db_session, p2_db.id, "10001-ALT")


# ==============================================================================
# 7. BARCODE LOOKUP SERVICE INTEGRATION TESTS
# ==============================================================================

class TestBarcodeLookupIntegration:
    """Verifies that services/barcode_lookup.py delegates cleanly to ProductDataService."""

    def test_barcode_lookup_delegation_to_product_data_service(self, isolated_db_session):
        from services.barcode_lookup import lookup_product
        from database.models import User

        # Ensure user 1 exists
        user = isolated_db_session.execute(select(User).where(User.id == 1)).scalar_one_or_none()
        if not user:
            isolated_db_session.execute(
                text("INSERT INTO users (id, email, hashed_password, created_at) VALUES (1, 'u1@example.com', 'pwd', '2026-01-01 00:00:00')")
            )
            isolated_db_session.commit()

        # Seed product via upsert_canonical_product
        now = datetime.now(timezone.utc)
        test_prod = CanonicalProduct(
            canonical_id="8901230007777",
            barcode="8901230007777",
            product_name="Integrated Wafer Biscuit",
            brand="BakeHouse",
            nutrition=Nutrition100g(calories=480.0, sugar=22.0, fat=18.0, salt=0.9),
            last_fetched_at=now,
        )
        upsert_canonical_product(isolated_db_session, test_prod)
        isolated_db_session.commit()

        res = lookup_product(isolated_db_session, "8901230007777", user_id=1)
        assert res is not None
        assert res["barcode"] == "8901230007777"
        assert res["product_name"] == "Integrated Wafer Biscuit"
        assert res["brand"] == "BakeHouse"
        assert "health_score" in res
        assert "final_decision" in res
