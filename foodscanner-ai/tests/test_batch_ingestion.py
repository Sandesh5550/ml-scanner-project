from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

# Ensure foodscanner-ai root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["SECRET_KEY"] = "test-secret-key-for-batch-ingestion-phase8"

from api.main import app
from database.canonical_adapter import db_product_to_canonical
from database.init_db import init_db
from database.models import Nutrition, Product, ScanHistory, User
from database.orm import SessionLocal, init_db as orm_init_db
from schemas.batch import (
    BatchActionTaken,
    BatchErrorCategory,
    BatchIngestionResult,
    BatchIngestRequest,
    BatchItemResult,
    BatchItemStatus,
    BatchJobResponse,
    BatchJobStatus,
    DEFAULT_MAX_BATCH_SIZE,
)
from schemas.product import (
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
)
from services import db_service
from services.auth_service import create_access_token, hash_password
from services.batch_ingestion_service import (
    BatchIngestionService,
    reset_default_batch_ingestion_service,
)
from services.db_service import get_enrichment_candidates, upsert_canonical_product
from services.off_client import OFFResult, OFFStatus, reset_default_off_client
from services.product_data_service import (
    ProductDataService,
    get_default_product_data_service,
    reset_default_product_data_service,
)
from services.sync_job_manager import (
    BatchJob,
    BatchJobManager,
    get_default_job_manager,
    reset_default_job_manager,
)


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema is initialized."""
    init_db()
    orm_init_db()


@pytest.fixture(autouse=True)
def isolate_job_manager_storage(tmp_path, monkeypatch):
    """Ensure all tests use an isolated temporary directory for batch job storage."""
    monkeypatch.setenv("BATCH_JOB_STORAGE_DIR", str(tmp_path))
    reset_default_job_manager()
    yield
    reset_default_job_manager()


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singletons before and after each test."""
    reset_default_off_client()
    reset_default_product_data_service()
    reset_default_batch_ingestion_service()
    reset_default_job_manager()
    yield
    reset_default_off_client()
    reset_default_product_data_service()
    reset_default_batch_ingestion_service()
    reset_default_job_manager()


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_user(db_session):
    """Create or retrieve a test user with a valid JWT token."""
    email = "phase8_batch_user@example.com"
    user = db_session.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Phase8 Batch User",
            hashed_password=hash_password("SecurePassword123!"),
            daily_calorie_limit=2000,
            age=30,
            weight=75.0,
            height=180.0,
            diet_type=None,
            created_at=db_service._utc_now_str(),
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    token = create_access_token(user_id=int(user.id))
    return {
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _make_test_canonical(
    barcode: str,
    name: str,
    calories: Optional[float] = 100.0,
    sugar: Optional[float] = 5.0,
    fat: Optional[float] = 2.0,
    salt: Optional[float] = 0.5,
    provenance: DataProvenance = DataProvenance.OFF_API_V3,
    confidence: DataConfidence = DataConfidence.HIGH,
    is_fresh: bool = True,
) -> CanonicalProduct:
    now_dt = datetime.now(timezone.utc)
    fetch_dt = now_dt if is_fresh else (now_dt - timedelta(days=40))

    nutrition = Nutrition100g(
        calories=calories,
        fat=fat,
        saturated_fat=1.0 if fat else None,
        trans_fat=0.0,
        sugar=sugar,
        salt=salt,
        sodium=salt * 0.4 if salt else None,
        protein=3.0,
        fiber=1.0,
        carbs=15.0,
    )
    field_prov = {
        "product_name": FieldProvenance(source=provenance, confidence=confidence, timestamp=fetch_dt),
        "nutrition": FieldProvenance(source=provenance, confidence=confidence, timestamp=fetch_dt),
    }
    return CanonicalProduct(
        canonical_id=barcode,
        barcode=barcode,
        alternate_barcodes=[],
        product_name=name,
        brand="Test Brand",
        category="Test Category",
        nutrition=nutrition,
        ingredients="Test Ingredients",
        additives=None,
        nutriscore="b",
        is_nutriscore_predicted=False,
        primary_provenance=provenance,
        field_provenance=field_prov,
        last_fetched_at=fetch_dt,
        last_updated_at=fetch_dt,
    )


# ==============================================================================
# 1. BATCH PRODUCT INGESTION TESTS
# ==============================================================================

class TestBatchIngestionService:
    """Verifies batch validation, normalization, deduplication, and fault isolation."""

    @pytest.mark.anyio
    async def test_empty_batch_handling(self):
        service = BatchIngestionService()
        result = await service.ingest_batch_async([])
        assert result.total_submitted == 0
        assert result.valid_identifiers == 0
        assert result.duplicate_identifiers == 0
        assert result.successfully_processed == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert len(result.items) == 0

    @pytest.mark.anyio
    async def test_invalid_barcode_rejection(self):
        service = BatchIngestionService()
        result = await service.ingest_batch_async(["", "  ", "ab", "12", "x" * 40])
        assert result.total_submitted == 5
        assert result.valid_identifiers == 0
        assert result.failed == 5
        for item in result.items:
            assert item.status == BatchItemStatus.FAILED
            assert item.action_taken == BatchActionTaken.INVALID_IDENTIFIER
            assert item.error_category == BatchErrorCategory.INVALID_IDENTIFIER

    @pytest.mark.anyio
    async def test_batch_deduplication(self, db_session):
        barcode = "8901234567890"
        canonical = _make_test_canonical(barcode, "Deduplication Product")
        upsert_canonical_product(db_session, canonical)
        db_session.commit()

        service = BatchIngestionService()
        # Submit batch containing identical barcode 3 times
        result = await service.ingest_batch_async([barcode, f" {barcode} ", barcode])
        assert result.total_submitted == 3
        assert result.valid_identifiers == 1
        assert result.duplicate_identifiers == 2
        assert result.successfully_processed == 1
        assert result.skipped == 2

        # First item was processed, subsequent marked SKIPPED
        assert result.items[0].status == BatchItemStatus.SUCCESS
        assert result.items[1].status == BatchItemStatus.SKIPPED
        assert result.items[1].action_taken == BatchActionTaken.DUPLICATE_IN_BATCH
        assert result.items[2].status == BatchItemStatus.SKIPPED

    @pytest.mark.anyio
    async def test_mixed_success_and_failure_isolation(self, db_session):
        valid_code = "8901111111111"
        not_found_code = "9999999999999"
        invalid_code = "bad"

        # Seed valid product
        upsert_canonical_product(db_session, _make_test_canonical(valid_code, "Good Product"))
        db_session.commit()

        # Mock ProductDataService to return None for not_found_code
        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = True
        mock_pds.get_product_by_barcode.side_effect = lambda db, barcode, force_refresh=False: (
            _make_test_canonical(valid_code, "Good Product") if barcode == valid_code else None
        )
        mock_pds.get_product_by_barcode_async = AsyncMock(
            side_effect=lambda db, barcode, force_refresh=False: (
                _make_test_canonical(valid_code, "Good Product") if barcode == valid_code else None
            )
        )

        service = BatchIngestionService(product_data_service=mock_pds)
        result = await service.ingest_batch_async([valid_code, not_found_code, invalid_code])

        assert result.total_submitted == 3
        assert result.successfully_processed == 1
        assert result.not_found == 1
        assert result.failed == 1

        # Failure of not_found_code and invalid_code did not stop valid_code
        assert result.items[0].status == BatchItemStatus.SUCCESS
        assert result.items[0].barcode == valid_code
        assert result.items[1].status == BatchItemStatus.NOT_FOUND
        assert result.items[2].status == BatchItemStatus.FAILED

    @pytest.mark.anyio
    async def test_batch_size_limit_enforced(self):
        service = BatchIngestionService()
        oversized = [f"8901{i:09d}" for i in range(101)]
        with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
            await service.ingest_batch_async(oversized)


# ==============================================================================
# 2. CONCURRENCY & ISOLATION TESTS
# ==============================================================================

class TestBatchConcurrency:
    """Verifies concurrency limits, semaphore ceilings, and session safety."""

    @pytest.mark.anyio
    async def test_concurrency_semaphore_ceiling_respected(self):
        active_concurrent_workers = 0
        max_seen_concurrency = 0
        lock = threading.Lock()

        async def _mock_get_async(db, barcode, force_refresh=False):
            nonlocal active_concurrent_workers, max_seen_concurrency
            with lock:
                active_concurrent_workers += 1
                if active_concurrent_workers > max_seen_concurrency:
                    max_seen_concurrency = active_concurrent_workers
            await asyncio.sleep(0.05)
            with lock:
                active_concurrent_workers -= 1
            return _make_test_canonical(barcode, f"Product {barcode}")

        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = False
        mock_pds.get_product_by_barcode.return_value = None
        mock_pds.get_product_by_barcode_async = AsyncMock(side_effect=_mock_get_async)

        # Set strict concurrency = 2
        service = BatchIngestionService(
            product_data_service=mock_pds,
            max_concurrency=2,
        )

        barcodes = [f"8902{i:09d}" for i in range(6)]
        result = await service.ingest_batch_async(barcodes, force_refresh=True)

        assert result.successfully_processed == 6
        assert max_seen_concurrency <= 2, f"Expected concurrency <= 2, saw {max_seen_concurrency}"

    @pytest.mark.anyio
    async def test_database_session_per_worker_isolation(self):
        created_sessions = []

        def _tracking_session_factory():
            s = MagicMock()
            created_sessions.append(s)
            return s

        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = False
        mock_pds.get_product_by_barcode.return_value = None
        mock_pds.get_product_by_barcode_async = AsyncMock(
            side_effect=lambda db, barcode, force_refresh=False: _make_test_canonical(barcode, "Test")
        )

        service = BatchIngestionService(
            product_data_service=mock_pds,
            session_factory=_tracking_session_factory,
            max_concurrency=3,
        )

        await service.ingest_batch_async(["8901000000001", "8901000000002", "8901000000003"], force_refresh=True)

        # Each worker opened and closed its own isolated session
        assert len(created_sessions) >= 3
        for s in created_sessions:
            s.close.assert_called()


# ==============================================================================
# 3. RETRY & FAILURE HANDLING TESTS
# ==============================================================================

class TestRetryAndFailureHandling:
    """Verifies transient error retries, backoff, Retry-After header, and 404 fast fail."""

    @pytest.mark.anyio
    async def test_transient_network_timeout_retries_and_succeeds(self):
        barcode = "8903000000001"
        attempt_count = 0
        sleep_durations = []

        async def _mock_sleep(sec):
            sleep_durations.append(sec)

        async def _flaky_get(db, barcode, force_refresh=False):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise asyncio.TimeoutError("Connection timed out")
            return _make_test_canonical(barcode, "Flaky Resolved")

        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = False
        mock_pds.get_product_by_barcode.return_value = None
        mock_pds.get_product_by_barcode_async = AsyncMock(side_effect=_flaky_get)

        service = BatchIngestionService(
            product_data_service=mock_pds,
            max_retries=3,
            base_backoff=0.1,
            sleep_func=_mock_sleep,
        )

        result = await service.ingest_batch_async([barcode], force_refresh=True)

        assert result.successfully_processed == 1
        item = result.items[0]
        assert item.status == BatchItemStatus.SUCCESS
        assert item.retries_attempted == 2
        assert len(sleep_durations) == 2
        assert attempt_count == 3

    @pytest.mark.anyio
    async def test_permanent_404_not_found_fails_fast_without_retries(self):
        barcode = "8903000000002"
        call_count = 0

        async def _not_found_get(db, barcode, force_refresh=False):
            nonlocal call_count
            call_count += 1
            return None  # OpenFoodFacts 404

        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = False
        mock_pds.get_product_by_barcode.return_value = None
        mock_pds.get_product_by_barcode_async = AsyncMock(side_effect=_not_found_get)

        service = BatchIngestionService(
            product_data_service=mock_pds,
            max_retries=3,
        )

        result = await service.ingest_batch_async([barcode], force_refresh=True)

        assert result.not_found == 1
        item = result.items[0]
        assert item.status == BatchItemStatus.NOT_FOUND
        assert item.action_taken == BatchActionTaken.NOT_FOUND
        assert item.error_category == BatchErrorCategory.NOT_FOUND
        assert item.retries_attempted == 0
        assert call_count == 1  # Failed fast in 1 call, zero redundant retries!

    @pytest.mark.anyio
    async def test_max_retries_exhaustion(self):
        barcode = "8903000000003"
        call_count = 0

        async def _always_fails(db, barcode, force_refresh=False):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Persistent 503 Service Unavailable")

        mock_pds = MagicMock(spec=ProductDataService)
        mock_pds.is_fresh.return_value = False
        mock_pds.get_product_by_barcode.return_value = None
        mock_pds.get_product_by_barcode_async = AsyncMock(side_effect=_always_fails)

        service = BatchIngestionService(
            product_data_service=mock_pds,
            max_retries=2,
            base_backoff=0.01,
            sleep_func=lambda s: None,
        )

        result = await service.ingest_batch_async([barcode], force_refresh=True)

        assert result.failed == 1
        item = result.items[0]
        assert item.status == BatchItemStatus.FAILED
        assert item.error_category == BatchErrorCategory.MAX_RETRIES_EXCEEDED
        assert item.retries_attempted == 2
        assert call_count == 3  # initial + 2 retries


# ==============================================================================
# 4. IDEMPOTENCY & DATA INTEGRITY TESTS
# ==============================================================================

class TestIdempotencyAndDataIntegrity:
    """Verifies that repeat ingestion preserves data, protects ADMIN_VERIFIED, and maintains freshness."""

    @pytest.mark.anyio
    async def test_repeated_ingestion_serves_cached_fresh(self, db_session):
        barcode = "8904000000001"
        canonical = _make_test_canonical(barcode, "Cached Parle", is_fresh=True)
        upsert_canonical_product(db_session, canonical)
        db_session.commit()

        mock_pds = get_default_product_data_service()
        service = BatchIngestionService(product_data_service=mock_pds)

        with patch.object(mock_pds.off_client, "get_product_async", new_callable=AsyncMock) as mock_off:
            result = await service.ingest_batch_async([barcode], force_refresh=False)

            assert result.successfully_processed == 1
            item = result.items[0]
            assert item.action_taken == BatchActionTaken.CACHED_FRESH
            assert item.is_fresh is True
            # Zero network egress to OFF
            mock_off.assert_not_called()

    @pytest.mark.anyio
    async def test_admin_verified_data_strictly_preserved(self, db_session):
        barcode = "8904000000002"
        # Seed ADMIN_VERIFIED product
        admin_canonical = _make_test_canonical(
            barcode=barcode,
            name="Admin Protected Amul",
            calories=720.0,
            fat=80.0,
            provenance=DataProvenance.ADMIN_VERIFIED,
            confidence=DataConfidence.HIGH,
            is_fresh=True,
        )
        upsert_canonical_product(db_session, admin_canonical)
        db_session.commit()

        # External OFF provides different (inferior) values
        incoming_off = _make_test_canonical(
            barcode=barcode,
            name="Generic Butter (Untrusted)",
            calories=600.0,
            fat=None,  # missing
            provenance=DataProvenance.OFF_API_V3,
            confidence=DataConfidence.LOW,
            is_fresh=True,
        )

        mock_pds = get_default_product_data_service()
        with patch.object(mock_pds.off_client, "get_product_async", new_callable=AsyncMock) as mock_off:
            mock_off.return_value = OFFResult(
                status=OFFStatus.SUCCESS,
                barcode=barcode,
                product=incoming_off,
                endpoint_used="api/v3",
            )
            service = BatchIngestionService(product_data_service=mock_pds)
            result = await service.ingest_batch_async([barcode], force_refresh=True)

            assert result.successfully_processed == 1

        # Query database to verify ADMIN_VERIFIED values were NOT overwritten
        refreshed = db_service.get_canonical_product_by_barcode(db_session, barcode)
        assert refreshed is not None
        assert refreshed.product_name == "Admin Protected Amul"
        assert refreshed.nutrition.calories == 720.0
        assert refreshed.nutrition.fat == 80.0
        assert refreshed.field_provenance["product_name"].source == DataProvenance.ADMIN_VERIFIED

    @pytest.mark.anyio
    async def test_explicit_zero_preserved_and_never_erased(self, db_session):
        barcode = "8904000000003"
        zero_cal_product = _make_test_canonical(
            barcode=barcode,
            name="Diet Coke Zero",
            calories=0.0,
            sugar=0.0,
            fat=0.0,
            salt=0.0,
            provenance=DataProvenance.OFF_API_V3,
            confidence=DataConfidence.HIGH,
        )
        upsert_canonical_product(db_session, zero_cal_product)
        db_session.commit()

        queried = db_service.get_canonical_product_by_barcode(db_session, barcode)
        assert queried.nutrition.calories == 0.0
        assert queried.nutrition.sugar == 0.0
        assert queried.nutrition.fat == 0.0


# ==============================================================================
# 5. BACKGROUND WORKER & JOB MANAGER TESTS
# ==============================================================================

class TestBackgroundJobManager:
    """Verifies persistent storage, stale job recovery, duplicate prevention, and retry."""

    def test_job_persistence_and_rehydration(self, tmp_path):
        mgr = BatchJobManager(storage_dir=tmp_path)
        barcodes = ["8905000000001", "8905000000002"]

        job = mgr.create_job(barcodes=barcodes, force_refresh=False)
        assert job.status == BatchJobStatus.PENDING

        # Rehydrate in a fresh manager instance
        mgr2 = BatchJobManager(storage_dir=tmp_path)
        loaded = mgr2.get_job(job.job_id)
        assert loaded is not None
        assert loaded.job_id == job.job_id
        assert loaded.barcodes == barcodes
        assert loaded.status == BatchJobStatus.PENDING

    def test_duplicate_active_job_prevention(self, tmp_path):
        mgr = BatchJobManager(storage_dir=tmp_path)
        barcodes = ["8905000000003", "8905000000004"]

        job1 = mgr.create_job(barcodes=barcodes, prevent_duplicates=True)
        job2 = mgr.create_job(barcodes=list(reversed(barcodes)), prevent_duplicates=True)

        # Returned existing active job instead of spawning duplicate
        assert job1.job_id == job2.job_id

    def test_stale_job_recovery_sweep(self, tmp_path):
        mgr = BatchJobManager(storage_dir=tmp_path)
        job = mgr.create_job(barcodes=["8905000000005"], prevent_duplicates=False)
        job.status = BatchJobStatus.RUNNING
        job.started_at = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        mgr._persist_job(job)

        # Trigger recovery with 600s threshold
        reaped = mgr.recover_stale_jobs(timeout_seconds=600)
        assert reaped == 1

        recovered_job = mgr.get_job(job.job_id)
        assert recovered_job.status == BatchJobStatus.FAILED
        assert recovered_job.error_category == BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value

    @pytest.mark.anyio
    async def test_job_execution_lifecycle_and_retry(self, tmp_path):
        valid_code = "8905000000006"
        fail_code = "8905000000007"

        mock_batch = MagicMock(spec=BatchIngestionService)

        async def _mock_ingest(barcodes, force_refresh=False, batch_id=None):
            items = []
            for b in barcodes:
                if b == valid_code:
                    items.append(
                        BatchItemResult(
                            barcode=b,
                            status=BatchItemStatus.SUCCESS,
                            product_name="Valid Item",
                            action_taken=BatchActionTaken.FETCHED,
                            is_fresh=True,
                        )
                    )
                else:
                    items.append(
                        BatchItemResult(
                            barcode=b,
                            status=BatchItemStatus.FAILED,
                            action_taken=BatchActionTaken.ERROR,
                            error_category=BatchErrorCategory.NETWORK_TIMEOUT,
                            error_message="Network unreachable",
                        )
                    )
            return BatchIngestionResult(
                batch_id=batch_id or "test",
                total_submitted=len(barcodes),
                valid_identifiers=len(barcodes),
                duplicate_identifiers=0,
                successfully_processed=1,
                failed=1,
                skipped=0,
                not_found=0,
                duration_seconds=0.1,
                created_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                items=items,
            )

        mock_batch.ingest_batch_async = AsyncMock(side_effect=_mock_ingest)
        mgr = BatchJobManager(storage_dir=tmp_path, batch_service=mock_batch)

        job = mgr.create_job(barcodes=[valid_code, fail_code])
        completed_job = await mgr.execute_job(job.job_id)

        assert completed_job.status == BatchJobStatus.COMPLETED
        assert completed_job.success_count == 1
        assert completed_job.failure_count == 1
        assert completed_job.failed_barcodes == [fail_code]

        # Trigger retry for failed items
        retry_job = mgr.retry_job(completed_job.job_id)
        assert retry_job.job_id != completed_job.job_id
        assert retry_job.barcodes == [fail_code]
        assert retry_job.attempt_count == 2
        assert retry_job.original_job_id == completed_job.job_id


# ==============================================================================
# 6. API ENDPOINTS & ZERO SCAN-HISTORY INVARIANT TESTS
# ==============================================================================

class TestBatchAPIEndpoints:
    """Verifies FastAPI endpoints: /batch/ingest, /batch/jobs/{id}, and /batch/jobs/{id}/retry."""

    def test_unauthenticated_request_rejected(self, client):
        res = client.post("/batch/ingest", json={"barcodes": ["8901234567890"]})
        assert res.status_code == 401

    def test_async_batch_ingestion_accepted(self, client, auth_user, tmp_path):
        payload = {
            "barcodes": ["8906000000001", "8906000000002"],
            "force_refresh": False,
            "async_job": True,
        }
        res = client.post("/batch/ingest", json=payload, headers=auth_user["headers"])
        assert res.status_code == 202
        data = res.json()
        assert "job_id" in data
        assert data["status"] == "PENDING"
        assert data["total_submitted"] == 2
        assert "/batch/jobs/" in data["status_url"]

    def test_sync_batch_ingestion_returns_full_result(self, client, auth_user, db_session):
        barcode = "8906000000003"
        upsert_canonical_product(db_session, _make_test_canonical(barcode, "Sync Product"))
        db_session.commit()

        payload = {
            "barcodes": [barcode],
            "force_refresh": False,
            "async_job": False,
        }
        res = client.post("/batch/ingest", json=payload, headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()
        assert data["total_submitted"] == 1
        assert data["successfully_processed"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["barcode"] == barcode

    def test_get_batch_job_status(self, client, auth_user):
        mgr = get_default_job_manager()
        job = mgr.create_job(barcodes=["8906000000004"])

        res = client.get(f"/batch/jobs/{job.job_id}", headers=auth_user["headers"])
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == job.job_id
        assert data["status"] == "PENDING"

    def test_get_nonexistent_job_returns_404(self, client, auth_user):
        res = client.get("/batch/jobs/job_nonexistent_999", headers=auth_user["headers"])
        assert res.status_code == 404

    def test_zero_scan_history_writes_during_batch_ingestion(self, client, auth_user, db_session):
        """CRITICAL INVARIANT: Batch operations must NEVER write to scan_history."""
        barcode = "8906000000005"
        upsert_canonical_product(db_session, _make_test_canonical(barcode, "Clean Catalog Item"))
        db_session.commit()

        initial_history_count = db_session.query(ScanHistory).count()

        payload = {
            "barcodes": [barcode],
            "force_refresh": False,
            "async_job": False,
        }
        res = client.post("/batch/ingest", json=payload, headers=auth_user["headers"])
        assert res.status_code == 200

        final_history_count = db_session.query(ScanHistory).count()
        assert final_history_count == initial_history_count, (
            "Invariant Violation: Batch ingestion wrote to scan_history!"
        )


# ==============================================================================
# 7. ENRICHMENT CANDIDATES QUERY & CLI TESTS
# ==============================================================================

class TestEnrichmentCandidatesAndCLI:
    """Verifies get_enrichment_candidates query helper and CLI script."""

    def test_get_enrichment_candidates_filtering(self, db_session):
        uniq = uuid.uuid4().hex[:6]
        stale_code = f"89071{uniq}"
        incomplete_code = f"89072{uniq}"
        fresh_code = f"89073{uniq}"

        # Stale product (fetched 45 days ago)
        stale_p = _make_test_canonical(stale_code, "Stale Product", is_fresh=False)
        upsert_canonical_product(db_session, stale_p)

        # Incomplete product (missing calories and sugar)
        incomplete_p = _make_test_canonical(
            incomplete_code,
            "Incomplete Product",
            calories=None,
            sugar=None,
            is_fresh=True,
        )
        upsert_canonical_product(db_session, incomplete_p)

        # Fresh and complete product
        fresh_p = _make_test_canonical(fresh_code, "Fresh Complete Product", is_fresh=True)
        upsert_canonical_product(db_session, fresh_p)
        db_session.commit()

        candidates = get_enrichment_candidates(db_session, limit=500)
        candidate_barcodes = {p.barcode for p in candidates}

        assert stale_code in candidate_barcodes
        assert incomplete_code in candidate_barcodes
        assert fresh_code not in candidate_barcodes

    def test_enrichment_cli_dry_run(self, monkeypatch):
        from scripts.enrich_incomplete_products import parse_arguments, run_enrichment

        test_args = ["enrich_incomplete_products.py", "--dry-run", "--limit", "5"]
        monkeypatch.setattr(sys, "argv", test_args)
        args = parse_arguments()
        exit_code = run_enrichment(args)
        assert exit_code == 0

    def test_enrichment_cli_explicit_barcodes_dry_run(self, monkeypatch):
        from scripts.enrich_incomplete_products import parse_arguments, run_enrichment

        test_args = ["enrich_incomplete_products.py", "--dry-run", "--barcodes", "8901030000000,8901491101837"]
        monkeypatch.setattr(sys, "argv", test_args)
        args = parse_arguments()
        assert args.barcodes == "8901030000000,8901491101837"
        exit_code = run_enrichment(args)
        assert exit_code == 0
