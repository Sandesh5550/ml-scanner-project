from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from database.canonical_adapter import merge_canonical_products
from database.models import Product
from schemas.product import (
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
)
from services.db_service import (
    get_canonical_product_by_barcode,
    upsert_canonical_product,
)
from services.indian_food_dataset import search_indian_dataset
from services.off_client import (
    OpenFoodFactsClient,
    canonical_to_legacy_dict,
    get_default_off_client,
    to_optional_float,
)
from services.product_search import (
    get_openfoodfacts_df,
    normalize_brand,
)

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_DAYS: int = 30


class _SyncFlight:
    """Thread-safe state container for in-flight synchronous barcode lookups."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[CanonicalProduct] = None
        self.waiters: int = 0
        self.done: bool = False


class _AsyncFlight:
    """Asyncio state container for in-flight asynchronous barcode lookups."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.result: Optional[CanonicalProduct] = None
        self.waiters: int = 0
        self.done: bool = False


class ProductDataService:
    """Unified DB-first product data service with quality-aware merging and caching.

    Core Invariants:
    1. Database-First: Always queries PostgreSQL/SQLite before initiating external API requests.
    2. Cache Freshness: Returns cached products if fresh (< cache_ttl_days). Stale data triggers
       an external refresh, but is returned gracefully if the external API fails (offline fallback).
    3. Missing Timestamp Protection: A missing last_fetched_at timestamp is never assumed fresh.
    4. Quality-Aware Merge: Merges incoming external data non-destructively; higher-quality and
       ADMIN_VERIFIED data are strictly protected.
    5. Zero Data Fabrication: Missing nutrition remains None (never 0.0). Explicit 0.0 is preserved.
    6. Product Identity Integrity: Refuses to merge different barcodes or variants.
    7. Single-Flight Concurrency: Deduplicates simultaneous in-flight requests for the same barcode.
    8. Graceful Fault Tolerance: Database read/write failures do not crash the user request.
    """

    def __init__(
        self,
        off_client: Optional[OpenFoodFactsClient] = None,
        cache_ttl_days: Optional[int] = None,
    ) -> None:
        self.off_client = off_client or get_default_off_client()

        # Determine TTL with validation against zero, negative, or invalid values
        resolved_ttl: int = DEFAULT_CACHE_TTL_DAYS
        raw_val = os.environ.get("OFF_CACHE_TTL_DAYS") or os.environ.get("CACHE_TTL_DAYS")
        if raw_val is not None and str(raw_val).strip():
            try:
                parsed = int(str(raw_val).strip())
                if parsed > 0:
                    resolved_ttl = parsed
                else:
                    logger.warning(
                        f"Non-positive cache TTL '{raw_val}' configured in environment; "
                        f"falling back to safe default {DEFAULT_CACHE_TTL_DAYS} days."
                    )
                    resolved_ttl = DEFAULT_CACHE_TTL_DAYS
            except (ValueError, TypeError):
                logger.warning(
                    f"Invalid cache TTL '{raw_val}' configured in environment; "
                    f"falling back to safe default {DEFAULT_CACHE_TTL_DAYS} days."
                )
                resolved_ttl = DEFAULT_CACHE_TTL_DAYS
        elif cache_ttl_days is not None:
            if isinstance(cache_ttl_days, int) and cache_ttl_days > 0:
                resolved_ttl = cache_ttl_days
            else:
                logger.warning(
                    f"Non-positive cache_ttl_days {cache_ttl_days} provided; "
                    f"falling back to safe default {DEFAULT_CACHE_TTL_DAYS} days."
                )
                resolved_ttl = DEFAULT_CACHE_TTL_DAYS

        self.cache_ttl_days = resolved_ttl

        # Sync single-flight coordination (bounded to currently active in-flight requests)
        self._flight_lock = threading.Lock()
        self._in_flight: Dict[str, _SyncFlight] = {}

        # Async single-flight coordination (bounded to currently active in-flight requests)
        self._async_flight_lock = asyncio.Lock()
        self._async_in_flight: Dict[str, _AsyncFlight] = {}

    def is_fresh(self, product: Optional[CanonicalProduct]) -> bool:
        """Evaluate whether a cached product disclosure is fresh according to cache_ttl_days.

        Rule: A product without a recorded fetch timestamp cannot be assumed fresh.
        """
        if product is None:
            return False
        ts = product.last_fetched_at
        if ts is None:
            return False

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - ts
        return age < timedelta(days=self.cache_ttl_days)

    def _lookup_csv_fallback(
        self,
        barcode: str,
        product_name_hint: Optional[str] = None,
    ) -> Optional[CanonicalProduct]:
        """Query local curated CSV datasets as offline fallback when DB and API miss."""
        clean_code = str(barcode).strip()

        # 1. Search OpenFoodFacts Cleaned CSV by barcode
        try:
            df = get_openfoodfacts_df()
            if df is not None and not df.empty and "code" in df.columns:
                matches = df[df["code"].astype(str) == clean_code]
                if not matches.empty:
                    row = matches.iloc[0]
                    raw_name = str(row.get("product_name") or "").strip()
                    if raw_name:
                        brand = normalize_brand(row.get("brands"))
                        grade = row.get("nutriscore_grade")
                        nutriscore_val = str(grade).strip().lower() if pd.notna(grade) else None
                        if nutriscore_val not in {"a", "b", "c", "d", "e"}:
                            nutriscore_val = None

                        def _csv_float(k: str) -> Optional[float]:
                            if k not in row or pd.isna(row[k]):
                                return None
                            return to_optional_float(row[k])

                        nutrition = Nutrition100g(
                            calories=_csv_float("calories"),
                            fat=_csv_float("fat"),
                            saturated_fat=_csv_float("saturated_fat"),
                            trans_fat=None,
                            carbs=_csv_float("carbs"),
                            sugar=_csv_float("sugar"),
                            protein=_csv_float("protein"),
                            fiber=_csv_float("fiber"),
                            salt=_csv_float("salt"),
                            sodium=None,
                        )

                        ingr = str(row.get("ingredients_text") or "").strip() if pd.notna(row.get("ingredients_text")) else None
                        additives = str(row.get("additives_tags") or "").strip() if pd.notna(row.get("additives_tags")) else None

                        now_dt = datetime.now(timezone.utc)
                        field_prov: Dict[str, FieldProvenance] = {}
                        for fname, val in [
                            ("product_name", raw_name),
                            ("brand", brand),
                            ("nutrition", nutrition),
                            ("ingredients", ingr),
                            ("additives", additives),
                        ]:
                            if val is not None:
                                field_prov[fname] = FieldProvenance(
                                    source=DataProvenance.OFF_CSV,
                                    confidence=DataConfidence.MEDIUM,
                                    timestamp=now_dt,
                                )

                        return CanonicalProduct(
                            canonical_id=clean_code,
                            barcode=clean_code,
                            alternate_barcodes=[],
                            product_name=raw_name,
                            brand=brand,
                            category=None,
                            nutrition=nutrition,
                            ingredients=ingr,
                            additives=additives,
                            nutriscore=nutriscore_val,
                            is_nutriscore_predicted=False,
                            primary_provenance=DataProvenance.OFF_CSV,
                            field_provenance=field_prov,
                            last_fetched_at=now_dt,
                            last_updated_at=now_dt,
                        )
        except Exception as exc:
            logger.warning(f"Error querying cleaned OpenFoodFacts CSV for barcode {clean_code}: {exc}")

        # 2. Search Indian packaged foods dataset by product_name_hint
        if product_name_hint:
            try:
                ind_res = search_indian_dataset(product_name_hint)
                if ind_res and ind_res.get("product_name"):
                    raw_name = str(ind_res["product_name"]).strip()
                    now_dt = datetime.now(timezone.utc)

                    nutrition = Nutrition100g(
                        calories=to_optional_float(ind_res.get("calories")),
                        fat=to_optional_float(ind_res.get("fat")),
                        saturated_fat=to_optional_float(ind_res.get("saturated_fat")),
                        trans_fat=to_optional_float(ind_res.get("trans_fat")),
                        carbs=to_optional_float(ind_res.get("carbs")),
                        sugar=to_optional_float(ind_res.get("sugar")),
                        protein=to_optional_float(ind_res.get("protein")),
                        fiber=to_optional_float(ind_res.get("fiber")),
                        salt=to_optional_float(ind_res.get("salt")),
                        sodium=None,
                    )

                    field_prov = {
                        "product_name": FieldProvenance(
                            source=DataProvenance.INDIAN_CSV,
                            confidence=DataConfidence.MEDIUM,
                            timestamp=now_dt,
                        ),
                        "nutrition": FieldProvenance(
                            source=DataProvenance.INDIAN_CSV,
                            confidence=DataConfidence.MEDIUM,
                            timestamp=now_dt,
                        ),
                    }

                    return CanonicalProduct(
                        canonical_id=clean_code,
                        barcode=clean_code,
                        alternate_barcodes=[],
                        product_name=raw_name,
                        brand=normalize_brand(ind_res.get("brand")),
                        category=None,
                        nutrition=nutrition,
                        ingredients=None,
                        additives=None,
                        nutriscore=None,
                        is_nutriscore_predicted=False,
                        primary_provenance=DataProvenance.INDIAN_CSV,
                        field_provenance=field_prov,
                        last_fetched_at=now_dt,
                        last_updated_at=now_dt,
                    )
            except Exception as exc:
                logger.warning(f"Error querying Indian foods dataset for hint '{product_name_hint}': {exc}")

        return None

    def get_product_by_barcode(
        self,
        db: Optional[Session],
        barcode: str,
        product_name_hint: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[CanonicalProduct]:
        """Retrieve a product by barcode following the authoritative DB-first hierarchy.

        Workflow:
        1. Check local database cache. If present and fresh (< 30 days) and not force_refresh:
           Return database record immediately (zero external network egress).
        2. If missing or stale or force_refresh:
           Synchronize flight and query OpenFoodFacts API v3 (with v2 fallback).
        3. If API fetch succeeds:
           Quality-aware merge with any existing record, persist to DB, and return.
        4. If API fetch fails (404, 429, 5xx, timeout, offline):
           - If stale database record exists: Return existing database record gracefully!
           - Else: Query curated CSV datasets as offline fallback, cache to DB, and return.
        """
        clean_code = str(barcode).strip()
        if not clean_code or not clean_code.isdigit() or len(clean_code) < 3:
            return None

        # 1. Check local database
        db_product: Optional[CanonicalProduct] = None
        if db is not None:
            try:
                db_product = get_canonical_product_by_barcode(db, clean_code)
            except Exception as exc:
                logger.warning(f"Database query failed for barcode {clean_code}: {exc}")
                db_product = None

        # 2. Return immediately if fresh
        if db_product is not None and not force_refresh and self.is_fresh(db_product):
            logger.info(f"Serving product {clean_code} from fresh database cache")
            return db_product

        # 3. Synchronize concurrent in-flight fetches for this barcode
        with self._flight_lock:
            if clean_code in self._in_flight:
                flight = self._in_flight[clean_code]
                flight.waiters += 1
                is_leader = False
            else:
                flight = _SyncFlight()
                self._in_flight[clean_code] = flight
                is_leader = True

        if not is_leader:
            # Wait for the leader thread to finish fetching & persisting
            flight.event.wait(timeout=10.0)
            with self._flight_lock:
                flight.waiters -= 1
                result = flight.result
            if result is not None:
                return result
            if db is not None:
                try:
                    return get_canonical_product_by_barcode(db, clean_code) or db_product
                except Exception:
                    return db_product
            return db_product

        # Leader thread execution
        final_result: Optional[CanonicalProduct] = None
        try:
            # Fetch from OpenFoodFacts
            off_result = self.off_client.get_product(clean_code)

            if off_result and off_result.is_success and off_result.product is not None:
                incoming_product = off_result.product
                if db_product is not None:
                    merged = merge_canonical_products(db_product, incoming_product)
                else:
                    merged = incoming_product

                # Persist to database
                if db is not None:
                    try:
                        merged = upsert_canonical_product(db, merged)
                        db.commit()
                    except Exception as exc:
                        logger.warning(f"Failed to persist product {clean_code} to DB: {exc}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                final_result = merged
                return final_result

            # API Fetch did NOT return a successful product (404, 429, 5xx, timeout)
            # If we already have a stale DB record, return it gracefully!
            if db_product is not None:
                logger.info(f"OpenFoodFacts fetch unsuccessful for {clean_code}; falling back to existing DB record")
                final_result = db_product
                return final_result

            # Not in DB and API failed -> Check Curated CSV fallback
            csv_product = self._lookup_csv_fallback(clean_code, product_name_hint=product_name_hint)
            if csv_product is not None:
                if db is not None:
                    try:
                        csv_product = upsert_canonical_product(db, csv_product)
                        db.commit()
                    except Exception as exc:
                        logger.warning(f"Failed to persist CSV product {clean_code} to DB: {exc}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                final_result = csv_product
                return final_result

            final_result = None
            return None

        finally:
            with self._flight_lock:
                flight.result = final_result
                flight.done = True
                flight.event.set()
                self._in_flight.pop(clean_code, None)

    async def get_product_by_barcode_async(
        self,
        db: Optional[Session],
        barcode: str,
        product_name_hint: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[CanonicalProduct]:
        """Asynchronously retrieve a product by barcode following the DB-first hierarchy."""
        clean_code = str(barcode).strip()
        if not clean_code or not clean_code.isdigit() or len(clean_code) < 3:
            return None

        # 1. Check local database
        db_product: Optional[CanonicalProduct] = None
        if db is not None:
            try:
                db_product = get_canonical_product_by_barcode(db, clean_code)
            except Exception as exc:
                logger.warning(f"Database query failed for barcode {clean_code}: {exc}")
                db_product = None

        # 2. Return immediately if fresh
        if db_product is not None and not force_refresh and self.is_fresh(db_product):
            logger.info(f"Serving product {clean_code} from fresh database cache (async)")
            return db_product

        # 3. Synchronize concurrent in-flight async tasks
        async with self._async_flight_lock:
            if clean_code in self._async_in_flight:
                flight = self._async_in_flight[clean_code]
                flight.waiters += 1
                is_leader = False
            else:
                flight = _AsyncFlight()
                self._async_in_flight[clean_code] = flight
                is_leader = True

        if not is_leader:
            try:
                await asyncio.wait_for(flight.event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            async with self._async_flight_lock:
                flight.waiters -= 1
                result = flight.result
            if result is not None:
                return result
            if db is not None:
                try:
                    return get_canonical_product_by_barcode(db, clean_code) or db_product
                except Exception:
                    return db_product
            return db_product

        final_result: Optional[CanonicalProduct] = None
        try:
            off_result = await self.off_client.get_product_async(clean_code)

            if off_result and off_result.is_success and off_result.product is not None:
                incoming_product = off_result.product
                if db_product is not None:
                    merged = merge_canonical_products(db_product, incoming_product)
                else:
                    merged = incoming_product

                if db is not None:
                    try:
                        merged = upsert_canonical_product(db, merged)
                        db.commit()
                    except Exception as exc:
                        logger.warning(f"Failed to persist product {clean_code} to DB: {exc}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                final_result = merged
                return final_result

            if db_product is not None:
                logger.info(f"OpenFoodFacts async fetch unsuccessful for {clean_code}; falling back to existing DB record")
                final_result = db_product
                return final_result

            csv_product = self._lookup_csv_fallback(clean_code, product_name_hint=product_name_hint)
            if csv_product is not None:
                if db is not None:
                    try:
                        csv_product = upsert_canonical_product(db, csv_product)
                        db.commit()
                    except Exception as exc:
                        logger.warning(f"Failed to persist CSV product {clean_code} to DB: {exc}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                final_result = csv_product
                return final_result

            final_result = None
            return None

        finally:
            async with self._async_flight_lock:
                flight.result = final_result
                flight.done = True
                flight.event.set()
                self._async_in_flight.pop(clean_code, None)

    def get_legacy_dict(
        self,
        db: Optional[Session],
        barcode: str,
        product_name_hint: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve product and map to legacy flat dictionary contract for existing callers."""
        canonical = self.get_product_by_barcode(
            db=db,
            barcode=barcode,
            product_name_hint=product_name_hint,
            force_refresh=force_refresh,
        )
        if canonical is None:
            return None
        return canonical_to_legacy_dict(canonical)

    async def get_legacy_dict_async(
        self,
        db: Optional[Session],
        barcode: str,
        product_name_hint: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Asynchronously retrieve product and map to legacy flat dictionary contract."""
        canonical = await self.get_product_by_barcode_async(
            db=db,
            barcode=barcode,
            product_name_hint=product_name_hint,
            force_refresh=force_refresh,
        )
        if canonical is None:
            return None
        return canonical_to_legacy_dict(canonical)


_DEFAULT_PRODUCT_DATA_SERVICE: Optional[ProductDataService] = None
_SERVICE_INIT_LOCK = threading.Lock()


def get_default_product_data_service() -> ProductDataService:
    """Retrieve or initialize the thread-safe global ProductDataService instance."""
    global _DEFAULT_PRODUCT_DATA_SERVICE
    if _DEFAULT_PRODUCT_DATA_SERVICE is not None:
        return _DEFAULT_PRODUCT_DATA_SERVICE

    with _SERVICE_INIT_LOCK:
        if _DEFAULT_PRODUCT_DATA_SERVICE is None:
            _DEFAULT_PRODUCT_DATA_SERVICE = ProductDataService()
        return _DEFAULT_PRODUCT_DATA_SERVICE


def reset_default_product_data_service() -> None:
    """Reset process-wide singleton ProductDataService instance (used in test isolation)."""
    global _DEFAULT_PRODUCT_DATA_SERVICE
    with _SERVICE_INIT_LOCK:
        if _DEFAULT_PRODUCT_DATA_SERVICE is not None:
            try:
                if hasattr(_DEFAULT_PRODUCT_DATA_SERVICE, "client") and hasattr(_DEFAULT_PRODUCT_DATA_SERVICE.client, "rate_limiter"):
                    _DEFAULT_PRODUCT_DATA_SERVICE.client.rate_limiter.reset()
            except Exception:
                pass
        _DEFAULT_PRODUCT_DATA_SERVICE = None
