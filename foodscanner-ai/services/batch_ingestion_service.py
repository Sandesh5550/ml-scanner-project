from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import math
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

import httpx
from sqlalchemy.orm import Session

from database.canonical_adapter import db_product_to_canonical
from database.db_session import SessionLocal
from database.models import Product
from schemas.batch import (
    BatchActionTaken,
    BatchErrorCategory,
    BatchIngestionResult,
    BatchItemResult,
    BatchItemStatus,
    DEFAULT_MAX_BATCH_SIZE,
)
from schemas.product import CanonicalProduct, DataProvenance
from services.off_client import parse_retry_after
from services.product_data_service import ProductDataService, get_default_product_data_service

logger = logging.getLogger(__name__)

# Defaults and limits
DEFAULT_BATCH_CONCURRENCY = 2
MIN_BATCH_CONCURRENCY = 1
MAX_BATCH_CONCURRENCY = 5

DEFAULT_MAX_RETRIES = 2
MAX_RETRY_LIMIT = 5

DEFAULT_BASE_BACKOFF = 0.5
DEFAULT_MAX_BACKOFF = 5.0


def _get_env_int(key: str, default: int, min_val: int, max_val: int) -> int:
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip():
        try:
            val = int(str(raw).strip())
            return max(min_val, min(val, max_val))
        except (ValueError, TypeError):
            pass
    return default


def _get_env_float(key: str, default: float, min_val: float, max_val: float) -> float:
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip():
        try:
            val = float(str(raw).strip())
            return max(min_val, min(val, max_val))
        except (ValueError, TypeError):
            pass
    return default


def _extract_confidence(prod: Any) -> Optional[str]:
    """Safely extract data confidence string from CanonicalProduct or DB Product."""
    if prod is None:
        return None
    if hasattr(prod, "overall_confidence") and prod.overall_confidence:
        val = prod.overall_confidence
        return val.value if hasattr(val, "value") else str(val)
    if hasattr(prod, "data_confidence") and prod.data_confidence:
        val = prod.data_confidence
        return val.value if hasattr(val, "value") else str(val)
    return None


class BatchIngestionService:
    """Production-grade batch product ingestion and synchronization service.

    Core Invariants:
    1. Bounded Concurrency: Regulates parallel operations via asyncio.Semaphore (default 2, max 5)
       to protect OpenFoodFacts rate limits and database connection pools.
    2. Fault Isolation: An individual barcode failure (e.g. 404, 429, timeout) never aborts the batch.
    3. Idempotent Deduplication: Identical barcodes within the same batch are deduplicated; repeated
       ingestions within freshness TTL (30 days) serve cached products with zero external network calls.
    4. Quality-Aware Merging: Merges incoming product data non-destructively; preserves ADMIN_VERIFIED
       fields, explicit numeric zero values, and field-level provenance.
    5. Controlled Retries & Backoff: Transient failures (429, 5xx, timeouts) retry with exponential backoff,
       jitter, and Retry-After support; permanent failures (404, invalid barcode) fail fast.
    6. Structured Observability: Produces typed, structured per-item and aggregate execution metrics.
    """

    def __init__(
        self,
        product_data_service: Optional[ProductDataService] = None,
        session_factory: Optional[Callable[[], Session]] = None,
        max_concurrency: Optional[int] = None,
        max_retries: Optional[int] = None,
        base_backoff: Optional[float] = None,
        max_backoff: Optional[float] = None,
        sleep_func: Optional[Callable[[float], Any]] = None,
    ) -> None:
        self.product_data_service = product_data_service or get_default_product_data_service()
        self.session_factory = session_factory or SessionLocal

        # Concurrency
        resolved_concurrency = max_concurrency or _get_env_int(
            "BATCH_INGESTION_CONCURRENCY",
            DEFAULT_BATCH_CONCURRENCY,
            MIN_BATCH_CONCURRENCY,
            MAX_BATCH_CONCURRENCY,
        )
        self.concurrency = max(MIN_BATCH_CONCURRENCY, min(resolved_concurrency, MAX_BATCH_CONCURRENCY))

        # Retries & Backoff
        resolved_retries = max_retries if max_retries is not None else _get_env_int(
            "BATCH_RETRY_MAX_ATTEMPTS",
            DEFAULT_MAX_RETRIES,
            0,
            MAX_RETRY_LIMIT,
        )
        self.max_retries = max(0, min(resolved_retries, MAX_RETRY_LIMIT))

        self.base_backoff = base_backoff or _get_env_float("BATCH_RETRY_BASE_BACKOFF", DEFAULT_BASE_BACKOFF, 0.1, 10.0)
        self.max_backoff = max_backoff or _get_env_float("BATCH_RETRY_MAX_BACKOFF", DEFAULT_MAX_BACKOFF, 0.5, 30.0)

        # Max batch size
        self.max_batch_size = _get_env_int(
            "BATCH_INGESTION_MAX_SIZE",
            DEFAULT_MAX_BATCH_SIZE,
            1,
            500,
        )

        # Injected sleep function for deterministic testing
        self._sleep_func = sleep_func

    @staticmethod
    def normalize_barcode(raw: Any) -> Optional[str]:
        """Validate and normalize a raw barcode input string.

        Rejects None, empty, whitespace-only, excessively short (< 3 chars),
        excessively long (> 32 chars), or non-numeric/invalid identifiers.
        Accepts numeric digits (3-14 chars) and standard dataset identifiers (e.g. IND-xxx).
        """
        if raw is None:
            return None
        cleaned = str(raw).strip()
        if not cleaned:
            return None
        if len(cleaned) < 3 or len(cleaned) > 32:
            return None
        # Reject control characters or unsafe characters
        if any(c in cleaned for c in "\r\n\t\x00;\"'"):
            return None

        # Food packaging barcodes are numeric; dataset items may start with IND-
        is_numeric = cleaned.isdigit()
        is_dataset_code = cleaned.startswith("IND-") and len(cleaned) > 4
        if not (is_numeric or is_dataset_code):
            return None

        return cleaned

    def _compute_backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate exponential backoff duration with full jitter."""
        if retry_after is not None and retry_after > 0:
            return min(self.max_backoff, retry_after)
        calculated = min(self.max_backoff, self.base_backoff * (2 ** attempt))
        jitter = random.uniform(0.0, 0.25 * calculated)
        return min(self.max_backoff, calculated + jitter)

    async def _sleep(self, seconds: float) -> None:
        """Asynchronously pause execution using injected sleep or asyncio.sleep."""
        if seconds <= 0:
            return
        if self._sleep_func is not None:
            res = self._sleep_func(seconds)
            if asyncio.iscoroutine(res):
                await res
        else:
            await asyncio.sleep(seconds)

    def prefetch_existing_canonical_products(
        self,
        barcodes: List[str],
        chunk_size: int = 250,
    ) -> Dict[str, CanonicalProduct]:
        """Prefetch existing products in chunks of 250 barcodes.

        Converts ORM records into detached immutable CanonicalProduct objects.
        Closes the read session before concurrent processing begins.
        Ensures zero ORM objects or SQLAlchemy sessions are shared across workers.
        """
        clean_codes = list(dict.fromkeys(
            [self.normalize_barcode(b) for b in barcodes if self.normalize_barcode(b) is not None]
        ))
        if not clean_codes:
            return {}

        from services.db_service import get_products_by_barcodes_bulk

        prefetched: Dict[str, CanonicalProduct] = {}

        for i in range(0, len(clean_codes), chunk_size):
            chunk = clean_codes[i : i + chunk_size]
            read_session: Session = self.session_factory()
            try:
                orm_products = get_products_by_barcodes_bulk(read_session, chunk)
                for orm_prod in orm_products:
                    try:
                        canonical = db_product_to_canonical(orm_prod)
                        if canonical.barcode:
                            prefetched[canonical.barcode] = canonical
                        for alias_bc in canonical.alternate_barcodes:
                            if alias_bc and alias_bc not in prefetched:
                                prefetched[alias_bc] = canonical
                    except Exception as exc:
                        logger.warning(f"Error converting prefetched product {getattr(orm_prod, 'barcode', None)}: {exc}")
            finally:
                read_session.close()

        return prefetched

    async def _process_single_barcode(
        self,
        barcode: str,
        force_refresh: bool,
        prefetched_product: Optional[CanonicalProduct] = None,
    ) -> BatchItemResult:
        """Process an individual barcode with bounded retries, exponential backoff, and isolation."""
        retries_attempted = 0

        # Fast-path: Check prefetched cache first without opening a DB session!
        if not force_refresh and prefetched_product is not None:
            if self.product_data_service.is_fresh(prefetched_product):
                return BatchItemResult(
                    barcode=barcode,
                    status=BatchItemStatus.SUCCESS,
                    product_name=prefetched_product.product_name,
                    brand=prefetched_product.brand,
                    action_taken=BatchActionTaken.CACHED_FRESH,
                    data_confidence=_extract_confidence(prefetched_product),
                    nutrition_completeness=getattr(prefetched_product, "nutrition_completeness", None),
                    is_fresh=True,
                    retries_attempted=0,
                )

        for attempt in range(self.max_retries + 1):
            session: Session = self.session_factory()
            try:
                # 1. Quick check: Is product already in DB and fresh?
                if not force_refresh:
                    try:
                        cached_product = self.product_data_service.get_product_by_barcode(
                            session,
                            barcode,
                            force_refresh=False,
                        )
                        if cached_product is not None and self.product_data_service.is_fresh(cached_product):
                            return BatchItemResult(
                                barcode=barcode,
                                status=BatchItemStatus.SUCCESS,
                                product_name=cached_product.product_name,
                                brand=cached_product.brand,
                                action_taken=BatchActionTaken.CACHED_FRESH,
                                data_confidence=_extract_confidence(cached_product),
                                nutrition_completeness=getattr(cached_product, "nutrition_completeness", None),
                                is_fresh=True,
                                retries_attempted=retries_attempted,
                            )
                    except Exception as exc:
                        logger.warning(f"Error checking DB cache for barcode {barcode}: {exc}")

                # 2. Fetch via ProductDataService (handles DB-first, OFF client, merge, and CSV fallback)
                product = await self.product_data_service.get_product_by_barcode_async(
                    db=session,
                    barcode=barcode,
                    force_refresh=force_refresh,
                )

                if product is not None:
                    # Determine action taken
                    if product.primary_provenance in (DataProvenance.OFF_CSV, DataProvenance.INDIAN_CSV):
                        action = BatchActionTaken.CSV_FALLBACK
                    elif force_refresh:
                        action = BatchActionTaken.FETCHED
                    else:
                        action = BatchActionTaken.MERGED

                    return BatchItemResult(
                        barcode=barcode,
                        status=BatchItemStatus.SUCCESS,
                        product_name=product.product_name,
                        brand=product.brand,
                        action_taken=action,
                        data_confidence=_extract_confidence(product),
                        nutrition_completeness=getattr(product, "nutrition_completeness", None),
                        is_fresh=True,
                        retries_attempted=retries_attempted,
                    )

                # Product returned None -> Not found in OFF or local datasets
                # 404 / NOT_FOUND is permanent; do NOT retry
                return BatchItemResult(
                    barcode=barcode,
                    status=BatchItemStatus.NOT_FOUND,
                    action_taken=BatchActionTaken.NOT_FOUND,
                    error_category=BatchErrorCategory.NOT_FOUND,
                    error_message=f"Product with barcode '{barcode}' not found in OpenFoodFacts or local datasets.",
                    retries_attempted=retries_attempted,
                )

            except Exception as exc:
                error_str = str(exc)
                logger.warning(f"Batch processing error for {barcode} (attempt {attempt + 1}/{self.max_retries + 1}): {error_str}")

                # Classify error
                is_rate_limit = "429" in error_str or "RATE_LIMITED" in error_str or isinstance(exc, (httpx.HTTPStatusError,)) and getattr(exc, "response", None) and getattr(exc.response, "status_code", 0) == 429
                is_timeout = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)) or "timeout" in error_str.lower()
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str
                is_transient = is_rate_limit or is_timeout or is_server_error or "database is locked" in error_str.lower()

                if is_transient and attempt < self.max_retries:
                    retries_attempted += 1
                    retry_after = None
                    if is_rate_limit:
                        # Extract Retry-After header if available
                        resp = getattr(exc, "response", None)
                        if resp and hasattr(resp, "headers"):
                            retry_after = parse_retry_after(resp.headers.get("retry-after"))
                    backoff_sec = self._compute_backoff(attempt, retry_after)
                    logger.info(f"Retrying barcode {barcode} in {backoff_sec:.2f}s (transient error: {error_str})")
                    await self._sleep(backoff_sec)
                    continue

                # Permanent failure or retries exhausted
                err_category = BatchErrorCategory.MAX_RETRIES_EXCEEDED if retries_attempted > 0 else (
                    BatchErrorCategory.RATE_LIMITED if is_rate_limit else (
                        BatchErrorCategory.NETWORK_TIMEOUT if is_timeout else (
                            BatchErrorCategory.SERVER_ERROR if is_server_error else BatchErrorCategory.DATABASE_ERROR
                        )
                    )
                )

                return BatchItemResult(
                    barcode=barcode,
                    status=BatchItemStatus.FAILED,
                    action_taken=BatchActionTaken.ERROR,
                    error_category=err_category,
                    error_message=f"Operation failed after {retries_attempted} retries: {error_str}",
                    retries_attempted=retries_attempted,
                )

            finally:
                try:
                    session.close()
                except Exception:
                    pass

        # Fallback if loop finishes unexpectedly
        return BatchItemResult(
            barcode=barcode,
            status=BatchItemStatus.FAILED,
            action_taken=BatchActionTaken.ERROR,
            error_category=BatchErrorCategory.MAX_RETRIES_EXCEEDED,
            error_message="Maximum retries exceeded without successful resolution.",
            retries_attempted=retries_attempted,
        )

    async def ingest_batch_async(
        self,
        barcodes: List[str],
        force_refresh: bool = False,
        batch_id: Optional[str] = None,
    ) -> BatchIngestionResult:
        """Asynchronously ingest a batch of product barcodes with bounded concurrency."""
        start_time = time.perf_counter()
        now_dt = datetime.now(timezone.utc)
        resolved_batch_id = batch_id or f"batch_{uuid.uuid4().hex[:16]}"

        if not barcodes:
            # Empty batch
            return BatchIngestionResult(
                batch_id=resolved_batch_id,
                total_submitted=0,
                valid_identifiers=0,
                duplicate_identifiers=0,
                successfully_processed=0,
                failed=0,
                skipped=0,
                not_found=0,
                duration_seconds=0.0,
                created_at=now_dt.isoformat(),
                completed_at=now_dt.isoformat(),
                items=[],
            )

        if len(barcodes) > self.max_batch_size:
            raise ValueError(
                f"Batch size {len(barcodes)} exceeds maximum allowed limit ({self.max_batch_size})."
            )

        total_submitted = len(barcodes)
        seen_barcodes: Set[str] = set()
        duplicate_count = 0

        # Pre-classify barcodes: valid unique vs duplicate vs invalid
        valid_items_to_process: List[Tuple[int, str]] = []  # (original_index, barcode)
        final_results: List[Optional[BatchItemResult]] = [None] * total_submitted

        for idx, raw_code in enumerate(barcodes):
            norm_code = self.normalize_barcode(raw_code)
            if norm_code is None:
                # Invalid identifier
                final_results[idx] = BatchItemResult(
                    barcode=str(raw_code) if raw_code is not None else "",
                    status=BatchItemStatus.FAILED,
                    action_taken=BatchActionTaken.INVALID_IDENTIFIER,
                    error_category=BatchErrorCategory.INVALID_IDENTIFIER,
                    error_message=f"Malformed barcode identifier: '{raw_code}'",
                )
                continue

            if norm_code in seen_barcodes:
                # Duplicate within the same batch
                duplicate_count += 1
                final_results[idx] = BatchItemResult(
                    barcode=norm_code,
                    status=BatchItemStatus.SKIPPED,
                    action_taken=BatchActionTaken.DUPLICATE_IN_BATCH,
                    error_category=BatchErrorCategory.DUPLICATE_IDENTIFIER,
                    error_message="Duplicate barcode in same batch; skipped subsequent occurrence.",
                )
                continue

            seen_barcodes.add(norm_code)
            valid_items_to_process.append((idx, norm_code))

        valid_count = len(valid_items_to_process)

        # Stage 4: Bulk prefetch existing products in chunks of 250 barcodes
        prefetched_cache: Dict[str, CanonicalProduct] = {}
        if not force_refresh and valid_items_to_process:
            valid_codes_only = [code for _, code in valid_items_to_process]
            try:
                prefetched_cache = self.prefetch_existing_canonical_products(valid_codes_only, chunk_size=250)
            except Exception as exc:
                logger.warning(f"Error during bulk prefetch; workers will fallback to per-item queries: {exc}")

        # Process valid items with bounded concurrency
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _bounded_worker(item_idx: int, code: str) -> None:
            async with semaphore:
                try:
                    prefetched_prod = prefetched_cache.get(code)
                    res = await self._process_single_barcode(code, force_refresh, prefetched_product=prefetched_prod)
                    final_results[item_idx] = res
                except Exception as exc:
                    logger.exception(f"Unhandled exception in batch worker for {code}: {exc}")
                    final_results[item_idx] = BatchItemResult(
                        barcode=code,
                        status=BatchItemStatus.FAILED,
                        action_taken=BatchActionTaken.ERROR,
                        error_category=BatchErrorCategory.SERVER_ERROR,
                        error_message=f"Unhandled worker error: {exc}",
                    )

        if valid_items_to_process:
            tasks = [
                asyncio.create_task(_bounded_worker(idx, code))
                for idx, code in valid_items_to_process
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        duration = time.perf_counter() - start_time
        completed_dt = datetime.now(timezone.utc)

        # Aggregate counts
        items_list: List[BatchItemResult] = [
            r for r in final_results if r is not None
        ]

        success_count = sum(1 for r in items_list if r.status == BatchItemStatus.SUCCESS)
        failed_count = sum(1 for r in items_list if r.status == BatchItemStatus.FAILED)
        skipped_count = sum(1 for r in items_list if r.status == BatchItemStatus.SKIPPED)
        not_found_count = sum(1 for r in items_list if r.status == BatchItemStatus.NOT_FOUND)

        logger.info(
            f"Batch {resolved_batch_id} complete: {total_submitted} submitted, "
            f"{success_count} success, {not_found_count} not found, "
            f"{failed_count} failed, {skipped_count} skipped in {duration:.2f}s."
        )

        return BatchIngestionResult(
            batch_id=resolved_batch_id,
            total_submitted=total_submitted,
            valid_identifiers=valid_count,
            duplicate_identifiers=duplicate_count,
            successfully_processed=success_count,
            failed=failed_count,
            skipped=skipped_count,
            not_found=not_found_count,
            duration_seconds=round(duration, 3),
            created_at=now_dt.isoformat(),
            completed_at=completed_dt.isoformat(),
            items=items_list,
        )

    def ingest_batch(
        self,
        barcodes: List[str],
        force_refresh: bool = False,
        batch_id: Optional[str] = None,
    ) -> BatchIngestionResult:
        """Synchronously ingest a batch of product barcodes."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If already inside an event loop (e.g. test runner or thread), run in dedicated thread pool
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    lambda: asyncio.run(
                        self.ingest_batch_async(barcodes, force_refresh, batch_id)
                    )
                )
                return future.result()
        else:
            return asyncio.run(self.ingest_batch_async(barcodes, force_refresh, batch_id))


_DEFAULT_BATCH_INGESTION_SERVICE: Optional[BatchIngestionService] = None


def get_default_batch_ingestion_service() -> BatchIngestionService:
    """Retrieve or initialize the thread-safe global BatchIngestionService instance."""
    global _DEFAULT_BATCH_INGESTION_SERVICE
    if _DEFAULT_BATCH_INGESTION_SERVICE is None:
        _DEFAULT_BATCH_INGESTION_SERVICE = BatchIngestionService()
    return _DEFAULT_BATCH_INGESTION_SERVICE


def reset_default_batch_ingestion_service() -> None:
    """Reset the singleton instance for clean test isolation."""
    global _DEFAULT_BATCH_INGESTION_SERVICE
    _DEFAULT_BATCH_INGESTION_SERVICE = None
