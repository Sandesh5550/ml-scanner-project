from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app, is_admin_user
from database.db_session import get_db
from database.models import User
from schemas.batch import (
    BatchActionTaken,
    BatchErrorCategory,
    BatchIngestionResult,
    BatchItemResult,
    BatchItemStatus,
    BatchJobStatus,
)
from services.auth_service import create_access_token
from services.batch_ingestion_service import BatchIngestionService
from services.sync_job_manager import BatchJob, BatchJobManager, get_default_job_manager


@pytest.fixture
def temp_job_manager():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_batch_mgr_"))
    mock_batch_service = MagicMock(spec=BatchIngestionService)
    mgr = BatchJobManager(storage_dir=temp_dir, batch_service=mock_batch_service)
    yield mgr, temp_dir
    mgr.shutdown()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_is_admin_user_detection(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "sysadmin@pramaan.ai, ops@pramaan.ai")

    u1 = User(id=1, email="user@example.com")
    assert is_admin_user(u1) is False

    u2 = User(id=2, email="sysadmin@pramaan.ai")
    assert is_admin_user(u2) is True

    u3 = User(id=3, email="admin@foodscanner.com")
    assert is_admin_user(u3) is True

    assert is_admin_user(None) is False


def test_batch_job_persists_user_ownership(temp_job_manager):
    mgr, _ = temp_job_manager
    job = mgr.create_job(
        barcodes=["8901234567890"],
        force_refresh=False,
        user_id=123,
    )
    assert job.user_id == 123

    # Reload from disk
    loaded = mgr.get_job(job.job_id)
    assert loaded is not None
    assert loaded.user_id == 123
    assert loaded.to_dict()["user_id"] == 123


def test_batch_job_retry_preserves_or_assigns_user(temp_job_manager):
    mgr, _ = temp_job_manager
    orig = mgr.create_job(
        barcodes=["8901234567890"],
        user_id=456,
    )
    orig.status = BatchJobStatus.FAILED
    orig.failed_barcodes = ["8901234567890"]

    # Retry without explicit user_id inherits original.user_id
    retry_job = mgr.retry_job(orig.job_id)
    assert retry_job.user_id == 456

    # Retry with explicit user_id updates user_id
    retry_job2 = mgr.retry_job(orig.job_id, user_id=789)
    assert retry_job2.user_id == 789


def test_single_worker_inter_process_lock(temp_job_manager):
    mgr, storage_dir = temp_job_manager

    job = mgr.create_job(barcodes=["8901234567890"], user_id=1)
    lock_file = storage_dir / f"{job.job_id}.lock"

    # Simulate another worker process holding the lock file
    with open(lock_file, "w") as f:
        f.write("99999 2026-11-12T12:00:00Z")

    # Calling execute_job should detect the lock and skip execution without error
    executed = asyncio.run(mgr.execute_job(job.job_id))
    assert executed.job_id == job.job_id
    # Underlying batch service was not called
    mgr.batch_service.ingest_batch_async.assert_not_called()

    # Clean up artificial lock file
    lock_file.unlink()


def test_stale_job_recovery_cleans_orphaned_locks(temp_job_manager):
    mgr, storage_dir = temp_job_manager

    job = mgr.create_job(barcodes=["8901234567890"], user_id=1)
    job.status = BatchJobStatus.RUNNING
    job.started_at = "2020-01-01T00:00:00Z"
    mgr._persist_job(job)

    lock_file = storage_dir / f"{job.job_id}.lock"
    lock_file.write_text("12345")

    # Recover stale jobs
    reaped = mgr.recover_stale_jobs(timeout_seconds=60)
    assert reaped == 1

    loaded = mgr.get_job(job.job_id)
    assert loaded.status == BatchJobStatus.FAILED
    assert loaded.error_category == BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value

    # Lock file should be cleaned up
    assert not lock_file.exists()


def test_batch_api_endpoint_authorization_matrix(monkeypatch):
    """Test full HTTP access control for owner, non-owner (404), and admin (200)."""
    monkeypatch.setenv("ADMIN_EMAILS", "admin_sec@pramaan.ai")
    monkeypatch.setenv("STRICT_BATCH_OWNERSHIP", "true")

    # Create users in DB
    user_a = User(id=101, email="usera@example.com", name="User A")
    user_b = User(id=102, email="userb@example.com", name="User B")
    admin_u = User(id=999, email="admin_sec@pramaan.ai", name="Admin")

    users_map = {101: user_a, 102: user_b, 999: admin_u}

    class MockQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return user_a

    mock_db = MagicMock()
    mock_db.query.return_value = MockQuery()

    def mock_get_current_user(creds=None):
        if not creds:
            from fastapi import HTTPException
            raise HTTPException(status_code=401)
        uid = int(creds.credentials.split(":")[1])
        return users_map[uid]

    app.dependency_overrides[get_db] = lambda: mock_db

    temp_dir = Path(tempfile.mkdtemp(prefix="test_batch_api_"))
    monkeypatch.setenv("BATCH_JOB_STORAGE_DIR", str(temp_dir))
    from services.sync_job_manager import reset_default_job_manager, get_default_job_manager
    reset_default_job_manager()
    mgr = get_default_job_manager()
    job = mgr.create_job(barcodes=["8901234567890"], user_id=101)
    job.status = BatchJobStatus.COMPLETED
    job.failed_barcodes = ["8901234567890"]
    mgr._persist_job(job)

    from services.auth_service import get_current_user

    client = TestClient(app)

    # 1. Owner (User A) accesses job -> 200
    app.dependency_overrides[get_current_user] = lambda: user_a
    resp = client.get(f"/batch/jobs/{job.job_id}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job.job_id

    # 2. Non-owner (User B) accesses job -> 404 (prevents ID enumeration)
    app.dependency_overrides[get_current_user] = lambda: user_b
    resp = client.get(f"/batch/jobs/{job.job_id}")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    # 3. Admin accesses User A's job -> 200
    app.dependency_overrides[get_current_user] = lambda: admin_u
    resp = client.get(f"/batch/jobs/{job.job_id}")
    assert resp.status_code == 200

    # 4. Non-owner (User B) retries User A's job -> 404
    app.dependency_overrides[get_current_user] = lambda: user_b
    resp = client.post(f"/batch/jobs/{job.job_id}/retry")
    assert resp.status_code == 404

    # 5. Owner (User A) retries job -> 202
    app.dependency_overrides[get_current_user] = lambda: user_a
    resp = client.post(f"/batch/jobs/{job.job_id}/retry")
    assert resp.status_code == 202
    retry_data = resp.json()
    assert retry_data["job_id"] != job.job_id

    # 6. Legacy Unowned Job (user_id is None): 404 for regular user, 200 for admin
    legacy_job = mgr.create_job(barcodes=["8909999999999"], user_id=None)
    legacy_job.status = BatchJobStatus.COMPLETED
    legacy_job.failed_barcodes = ["8909999999999"]
    mgr._persist_job(legacy_job)

    # Regular user receives 404 (isolation of legacy jobs)
    app.dependency_overrides[get_current_user] = lambda: user_b
    resp = client.get(f"/batch/jobs/{legacy_job.job_id}")
    assert resp.status_code == 404

    resp = client.post(f"/batch/jobs/{legacy_job.job_id}/retry")
    assert resp.status_code == 404

    # Admin user receives 200 / 202
    app.dependency_overrides[get_current_user] = lambda: admin_u
    resp = client.get(f"/batch/jobs/{legacy_job.job_id}")
    assert resp.status_code == 200

    resp = client.post(f"/batch/jobs/{legacy_job.job_id}/retry")
    assert resp.status_code == 202

    reset_default_job_manager()
    shutil.rmtree(temp_dir, ignore_errors=True)
    app.dependency_overrides.clear()


class MockRedisLockClient:
    """Mock Redis client simulating atomic lock acquisition, compare-and-delete, and compare-and-expire."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.should_fail = False

    def set(self, key: str, value: Any, nx: bool = False, ex: Optional[int] = None) -> bool:
        if self.should_fail:
            raise ConnectionError("Redis disconnected")
        if nx and key in self.keys:
            return False
        self.keys[key] = str(value)
        if ex is not None:
            self.expiries[key] = int(ex)
        return True

    def get(self, key: str) -> Optional[str]:
        if self.should_fail:
            raise ConnectionError("Redis disconnected")
        return self.keys.get(key)

    def delete(self, key: str) -> int:
        if self.should_fail:
            raise ConnectionError("Redis disconnected")
        self.expiries.pop(key, None)
        return 1 if self.keys.pop(key, None) is not None else 0

    def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        if self.should_fail:
            raise ConnectionError("Redis disconnected")
        # Compare-and-delete
        if "del" in script:
            expected_token = str(args[0])
            if self.keys.get(key) == expected_token:
                self.keys.pop(key, None)
                self.expiries.pop(key, None)
                return 1
            return 0
        # Compare-and-expire
        if "expire" in script:
            expected_token = str(args[0])
            new_ttl = int(args[1])
            if self.keys.get(key) == expected_token:
                self.expiries[key] = new_ttl
                return 1
            return 0
        return 0


def test_redis_distributed_batch_job_locking(temp_job_manager):
    """Verify BatchJobManager acquires and releases Redis lock across execute_job lifecycle."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    # Configure batch service to return successful result
    mgr.batch_service.ingest_batch_async = AsyncMock(
        return_value=BatchIngestionResult(
            batch_id="test_b",
            total_submitted=1,
            valid_identifiers=1,
            duplicate_identifiers=0,
            successfully_processed=1,
            failed=0,
            skipped=0,
            not_found=0,
            duration_seconds=0.05,
            created_at="2026-11-12T00:00:00Z",
            completed_at="2026-11-12T00:00:01Z",
            items=[],
        )
    )

    job = mgr.create_job(barcodes=["8901111111111"], user_id=1)
    lock_key = f"lock:batch:{job.job_id}"

    # 1. Execute job - acquires Redis lock and releases it in finally block
    asyncio.run(mgr.execute_job(job.job_id))
    # After completion, lock is cleanly released
    assert lock_key not in mock_redis.keys

    # 2. If another container already holds the Redis lock:
    mock_redis.keys[lock_key] = "remote_worker_token"
    job2 = mgr.create_job(barcodes=["8902222222222"], user_id=1)
    lock_key2 = f"lock:batch:{job2.job_id}"
    mock_redis.keys[lock_key2] = "remote_worker"

    # Execution detects Redis lock and skips redundant execution
    result = asyncio.run(mgr.execute_job(job2.job_id))
    assert result.job_id == job2.job_id


def test_ownership_safe_redis_lock_release():
    """Verify worker A's expired lock cannot delete worker B's newly acquired lock."""
    from services.sync_job_manager import release_redis_lock

    mock_redis = MockRedisLockClient()
    lock_key = "lock:batch:job_concurrent_xyz"

    # 1. Worker A acquires lock with Token A
    token_a = "worker_a_token_111"
    assert mock_redis.set(lock_key, token_a, nx=True, ex=600) is True

    # 2. Worker A's lock expires in Redis (simulated by removal)
    mock_redis.delete(lock_key)

    # 3. Worker B acquires the replacement lock with Token B
    token_b = "worker_b_token_222"
    assert mock_redis.set(lock_key, token_b, nx=True, ex=600) is True
    assert mock_redis.get(lock_key) == token_b

    # 4. Worker A finishes late and attempts to release its old lock using Token A
    released = release_redis_lock(mock_redis, lock_key, token_a)

    # 5. Lock release fails for Worker A, and Worker B's lock remains completely intact
    assert released is False
    assert mock_redis.get(lock_key) == token_b

    # 6. Worker B releases its own lock using Token B -> succeeds
    released_b = release_redis_lock(mock_redis, lock_key, token_b)
    assert released_b is True
    assert mock_redis.get(lock_key) is None


def test_ownership_safe_redis_lock_renewal():
    """Verify lock renewal succeeds for owner and fails for non-owner or disconnected Redis."""
    from services.sync_job_manager import renew_redis_lock

    mock_redis = MockRedisLockClient()
    lock_key = "lock:batch:job_renewal_123"
    owner_token = "owner_token_abc"
    wrong_token = "intruder_token_xyz"

    # Acquire lock with 600s TTL
    assert mock_redis.set(lock_key, owner_token, nx=True, ex=600) is True
    assert mock_redis.expiries[lock_key] == 600

    # 1. Non-owner attempts renewal -> rejected, TTL unchanged
    assert renew_redis_lock(mock_redis, lock_key, wrong_token, 1200) is False
    assert mock_redis.expiries[lock_key] == 600

    # 2. Owner renews lock -> extends TTL to 1200s
    assert renew_redis_lock(mock_redis, lock_key, owner_token, 1200) is True
    assert mock_redis.expiries[lock_key] == 1200

    # 3. Redis disconnected during renewal -> returns False without throwing
    mock_redis.should_fail = True
    assert renew_redis_lock(mock_redis, lock_key, owner_token, 1800) is False


def test_long_running_batch_with_lock_renewal(temp_job_manager):
    """Verify background renewal coroutine keeps lock alive during long-running execution."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    async def mock_slow_ingestion(*args, **kwargs):
        # Simulate work duration
        await asyncio.sleep(0.05)
        return BatchIngestionResult(
            batch_id="test_slow",
            total_submitted=1,
            valid_identifiers=1,
            duplicate_identifiers=0,
            successfully_processed=1,
            failed=0,
            skipped=0,
            not_found=0,
            duration_seconds=0.05,
            created_at="2026-11-12T00:00:00Z",
            completed_at="2026-11-12T00:00:01Z",
            items=[],
        )

    mgr.batch_service.ingest_batch_async = mock_slow_ingestion

    job = mgr.create_job(barcodes=["8905555555555"], user_id=1)
    lock_key = f"lock:batch:{job.job_id}"

    completed_job = asyncio.run(mgr.execute_job(job.job_id))
    assert completed_job.status == BatchJobStatus.COMPLETED

    # Upon completion, renewal task is cleaned up and lock is cleanly released
    assert lock_key not in mock_redis.keys


def test_renewal_temporary_disconnect_recovers_before_deadline(temp_job_manager):
    """Verify temporary Redis disconnect recovers if reconnected before safety deadline."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    # Ingestion simulates work while Redis temporarily disconnects and reconnects
    async def mock_work_with_temporary_disconnect(*args, **kwargs):
        mock_redis.should_fail = True
        await asyncio.sleep(0.05)
        mock_redis.should_fail = False
        await asyncio.sleep(0.05)
        return BatchIngestionResult(
            batch_id="test_reconnect",
            total_submitted=1,
            valid_identifiers=1,
            duplicate_identifiers=0,
            successfully_processed=1,
            failed=0,
            skipped=0,
            not_found=0,
            duration_seconds=0.1,
            created_at="2026-11-12T00:00:00Z",
            completed_at="2026-11-12T00:00:01Z",
            items=[],
        )

    mgr.batch_service.ingest_batch_async = mock_work_with_temporary_disconnect

    job = mgr.create_job(barcodes=["8907777777777"], user_id=1)
    res = asyncio.run(mgr.execute_job(job.job_id))
    assert res.status == BatchJobStatus.COMPLETED


def test_renewal_confirmed_lock_lost_aborts_immediately(temp_job_manager, monkeypatch):
    """Verify that if Redis reports lock was acquired by another token (LOST), execution aborts immediately."""
    monkeypatch.setenv("BATCH_LOCK_RENEW_INTERVAL", "0.05")
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    ingestion_cancelled = False

    async def mock_ingestion_interrupted(*args, **kwargs):
        nonlocal ingestion_cancelled
        try:
            # Another worker steals the lock
            job_key = f"lock:batch:{job.job_id}"
            mock_redis.keys[job_key] = "stolen_by_worker_b"
            # Sleep long enough for renewal to detect stolen lock
            await asyncio.sleep(0.5)
            return BatchIngestionResult(
                batch_id="should_never_finish",
                total_submitted=1,
                valid_identifiers=1,
                duplicate_identifiers=0,
                successfully_processed=1,
                failed=0,
                skipped=0,
                not_found=0,
                duration_seconds=0.5,
                created_at="2026-11-12T00:00:00Z",
                completed_at="2026-11-12T00:00:01Z",
                items=[],
            )
        except asyncio.CancelledError:
            ingestion_cancelled = True
            raise

    mgr.batch_service.ingest_batch_async = mock_ingestion_interrupted

    job = mgr.create_job(barcodes=["8908888888888"], user_id=1)
    res = asyncio.run(mgr.execute_job(job.job_id))

    # Ingestion was cancelled
    assert ingestion_cancelled is True
    # Job aborted with lock loss failure
    assert res.status == BatchJobStatus.FAILED
    assert "lock" in res.last_error.lower()
    # Worker B's stolen lock remains untouched in Redis
    assert mock_redis.get(f"lock:batch:{job.job_id}") == "stolen_by_worker_b"


def test_lost_lock_worker_cannot_overwrite_newer_worker_state(temp_job_manager):
    """Verify an aborted worker whose lock was stolen cannot overwrite a newer worker's state on disk."""
    import json
    mgr, storage_dir = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8909999999999"], user_id=1)
    lock_key = f"lock:batch:{job.job_id}"

    async def mock_ingestion_with_newer_worker_update(*args, **kwargs):
        # 1. Stolen lock in Redis
        mock_redis.keys[lock_key] = "worker_b_replacement"

        # 2. Worker B executes and updates the job on disk with newer started_at and COMPLETED status
        job_file = storage_dir / f"{job.job_id}.json"
        with open(job_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": job.job_id,
                "status": "COMPLETED",
                "barcodes": ["8909999999999"],
                "force_refresh": False,
                "total_submitted": 1,
                "processed_count": 1,
                "success_count": 1,
                "failure_count": 0,
                "skipped_count": 0,
                "not_found_count": 0,
                "started_at": "2026-11-12T12:00:00Z",  # Different started_at
                "completed_at": "2026-11-12T12:00:05Z",
                "execution_token": "worker_b_replacement",
            }, f)

        # Wait for renewal to detect lost lock and cancel
        await asyncio.sleep(0.5)

    mgr.batch_service.ingest_batch_async = mock_ingestion_with_newer_worker_update

    res = asyncio.run(mgr.execute_job(job.job_id))
    assert res.status == BatchJobStatus.FAILED

    # Check on-disk state: Worker B's completed status must NOT have been overwritten
    job_file = storage_dir / f"{job.job_id}.json"
    with open(job_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)

    assert on_disk["status"] == "COMPLETED"
    assert on_disk["started_at"] == "2026-11-12T12:00:00Z"
    assert mock_redis.get(lock_key) == "worker_b_replacement"


def test_renewal_loop_unreachable_redis_expires_at_safety_deadline():
    """Verify that if Redis remains down, the renewal loop fails closed when safety deadline expires."""
    from services.sync_job_manager import BatchJobManager
    mock_redis = MockRedisLockClient()
    mock_redis.should_fail = True  # Redis is completely down
    stop_event = asyncio.Event()
    abort_event = asyncio.Event()
    abort_info = {}

    async def run_loop():
        await BatchJobManager._lock_renew_loop(
            client=mock_redis,
            lock_key="lock:test_deadline",
            lock_token="token_deadline",
            ttl_seconds=1,  # 1 second TTL
            interval_seconds=0.1,  # check every 100ms
            stop_event=stop_event,
            abort_event=abort_event,
            abort_info=abort_info,
        )

    asyncio.run(run_loop())
    assert abort_event.is_set() is True
    assert abort_info.get("reason") in ("SAFETY_DEADLINE_EXCEEDED", "REDIS_UNREACHABLE_DEADLINE_EXPIRED")


def test_execution_token_is_persisted_and_restored(temp_job_manager):
    """1. Verify execution_token is persisted to disk and restored during deserialization."""
    mgr, _ = temp_job_manager
    job = mgr.create_job(barcodes=["8901112223334"], user_id=1)
    job.execution_token = "exec_token_abc_123"
    job.status = BatchJobStatus.RUNNING
    persisted = mgr._persist_job(job)
    assert persisted is True

    # Reload from disk
    loaded = mgr.get_job(job.job_id)
    assert loaded is not None
    assert loaded.execution_token == "exec_token_abc_123"
    assert loaded.to_dict()["execution_token"] == "exec_token_abc_123"


def test_old_worker_cannot_overwrite_newer_worker_running_state(temp_job_manager):
    """2. Verify an older worker with token A cannot overwrite a newer worker's RUNNING state with token B."""
    import json
    mgr, storage_dir = temp_job_manager

    job = mgr.create_job(barcodes=["8902223334445"], user_id=1)
    job_file = storage_dir / f"{job.job_id}.json"

    # Worker B acquires the job and writes its state
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump({
            "job_id": job.job_id,
            "status": "RUNNING",
            "barcodes": ["8902223334445"],
            "force_refresh": False,
            "total_submitted": 1,
            "execution_token": "worker_b_token",
            "started_at": "2026-11-12T12:05:00Z",
        }, f)

    # Worker A attempts to persist with token A
    job.execution_token = "worker_a_token"
    job.status = BatchJobStatus.RUNNING
    persisted = mgr._persist_job(job)

    assert persisted is False

    with open(job_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["execution_token"] == "worker_b_token"


def test_old_worker_cannot_overwrite_newer_worker_completed_state(temp_job_manager):
    """3. Verify an older worker cannot overwrite a newer worker's COMPLETED state."""
    import json
    mgr, storage_dir = temp_job_manager

    job = mgr.create_job(barcodes=["8903334445556"], user_id=1)
    job_file = storage_dir / f"{job.job_id}.json"

    # Worker B finishes the job as COMPLETED
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump({
            "job_id": job.job_id,
            "status": "COMPLETED",
            "barcodes": ["8903334445556"],
            "force_refresh": False,
            "total_submitted": 1,
            "success_count": 1,
            "execution_token": "worker_b_token",
            "completed_at": "2026-11-12T12:10:00Z",
        }, f)

    # Worker A attempts to mark FAILED with token A
    job.execution_token = "worker_a_token"
    job.status = BatchJobStatus.FAILED
    job.last_error = "Worker A lock expired"
    persisted = mgr._persist_job(job)

    assert persisted is False

    with open(job_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["status"] == "COMPLETED"
    assert on_disk["execution_token"] == "worker_b_token"


def test_generic_exception_from_old_worker_cannot_overwrite_newer_worker(temp_job_manager):
    """4. Verify a generic exception in an older worker cannot overwrite a newer worker's state."""
    import json
    mgr, storage_dir = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8904445556667"], user_id=1)
    lock_key = f"lock:batch:{job.job_id}"

    async def mock_crashing_worker(*args, **kwargs):
        # While Worker A is working, Worker B steals the Redis lock and completes the job on disk
        mock_redis.keys[lock_key] = "worker_b_token"
        job_file = storage_dir / f"{job.job_id}.json"
        with open(job_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": job.job_id,
                "status": "COMPLETED",
                "barcodes": ["8904445556667"],
                "force_refresh": False,
                "total_submitted": 1,
                "success_count": 1,
                "execution_token": "worker_b_token",
                "completed_at": "2026-11-12T12:15:00Z",
            }, f)
        # Worker A encounters an unhandled unexpected runtime error
        raise RuntimeError("Disk full or catastrophic system error in Worker A")

    mgr.batch_service.ingest_batch_async = mock_crashing_worker

    failed_job = asyncio.run(mgr.execute_job(job.job_id))
    assert failed_job.status == BatchJobStatus.FAILED

    # On disk: Worker B's COMPLETED state was protected from Worker A's generic exception handler
    job_file = storage_dir / f"{job.job_id}.json"
    with open(job_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)

    assert on_disk["status"] == "COMPLETED"
    assert on_disk["execution_token"] == "worker_b_token"


def test_legacy_job_json_remains_readable(temp_job_manager):
    """5. Verify backward compatibility: legacy job JSON without execution_token is read cleanly."""
    import json
    mgr, storage_dir = temp_job_manager

    legacy_file = storage_dir / "job_legacy_123.json"
    with open(legacy_file, "w", encoding="utf-8") as f:
        json.dump({
            "job_id": "job_legacy_123",
            "status": "COMPLETED",
            "barcodes": ["8905556667778"],
            "force_refresh": False,
            "total_submitted": 1,
            "success_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            # Note: No execution_token field
        }, f)

    loaded = mgr.get_job("job_legacy_123")
    assert loaded is not None
    assert loaded.job_id == "job_legacy_123"
    assert loaded.execution_token is None
    assert loaded.status == BatchJobStatus.COMPLETED


def test_legacy_job_cannot_bypass_ownership_protection(temp_job_manager):
    """6. Verify an unowned/legacy update (execution_token=None) cannot overwrite an owned job."""
    import json
    mgr, storage_dir = temp_job_manager

    job = mgr.create_job(barcodes=["8906667778889"], user_id=1)
    job_file = storage_dir / f"{job.job_id}.json"

    # Disk is owned by an active execution token
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump({
            "job_id": job.job_id,
            "status": "RUNNING",
            "barcodes": ["8906667778889"],
            "force_refresh": False,
            "total_submitted": 1,
            "execution_token": "active_worker_token",
        }, f)

    # Legacy caller attempts to update without execution_token
    job.execution_token = None
    job.status = BatchJobStatus.FAILED
    persisted = mgr._persist_job(job)

    assert persisted is False

    with open(job_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["status"] == "RUNNING"
    assert on_disk["execution_token"] == "active_worker_token"


def test_active_renewed_lock_prevents_stale_reaping(temp_job_manager):
    """7. Verify recover_stale_jobs preserves a RUNNING job whose Redis lock is actively renewed."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8907778889990"], user_id=1)
    job.status = BatchJobStatus.RUNNING
    job.started_at = "2020-01-01T00:00:00Z"  # Started long ago (> 600s)
    job.execution_token = "live_worker_token"
    mgr._persist_job(job)

    # Active worker is holding and renewing the lock in Redis
    lock_key = f"lock:batch:{job.job_id}"
    mock_redis.keys[lock_key] = "live_worker_token"

    reaped = mgr.recover_stale_jobs(timeout_seconds=600)

    # Reaped count is 0 because the lock was actively held in Redis
    assert reaped == 0
    loaded = mgr.get_job(job.job_id)
    assert loaded.status == BatchJobStatus.RUNNING


def test_expired_lock_permits_safe_recovery(temp_job_manager):
    """8. Verify recover_stale_jobs reaps a stranded job when its Redis lock has expired."""
    mgr, storage_dir = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8908889990001"], user_id=1)
    job.status = BatchJobStatus.RUNNING
    job.started_at = "2020-01-01T00:00:00Z"  # Stale (> 600s)
    job.execution_token = "crashed_worker_token"
    mgr._persist_job(job)

    # Redis lock has expired (key is absent)
    lock_key = f"lock:batch:{job.job_id}"
    assert lock_key not in mock_redis.keys

    reaped = mgr.recover_stale_jobs(timeout_seconds=600)

    assert reaped == 1
    loaded = mgr.get_job(job.job_id)
    assert loaded.status == BatchJobStatus.FAILED
    assert loaded.error_category == BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value
    # Recovery lock was released after reaping
    assert lock_key not in mock_redis.keys


def test_reaper_and_new_worker_racing_cannot_corrupt_state(temp_job_manager):
    """9. Verify racing between reaper and worker does not corrupt state."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8909990001112"], user_id=1)
    job.status = BatchJobStatus.RUNNING
    job.started_at = "2020-01-01T00:00:00Z"
    mgr._persist_job(job)

    lock_key = f"lock:batch:{job.job_id}"

    # Scenario A: New worker acquires lock just before reaper checks
    mock_redis.keys[lock_key] = "new_worker_token"
    reaped = mgr.recover_stale_jobs(timeout_seconds=600)
    assert reaped == 0
    assert mock_redis.get(lock_key) == "new_worker_token"


def test_redis_disconnection_during_recovery_fails_safely(temp_job_manager):
    """10. Verify Redis communication failure during stale sweep fails closed without state corruption."""
    mgr, _ = temp_job_manager
    mock_redis = MockRedisLockClient()
    mock_redis.should_fail = True  # Redis is completely disconnected
    mgr.set_redis_client(mock_redis)

    job = mgr.create_job(barcodes=["8900001112223"], user_id=1)
    job.status = BatchJobStatus.RUNNING
    job.started_at = "2020-01-01T00:00:00Z"
    mgr._persist_job(job)

    reaped = mgr.recover_stale_jobs(timeout_seconds=600)

    # Failed closed: zero jobs reaped, no unhandled exception
    assert reaped == 0
    loaded = mgr.get_job(job.job_id)
    assert loaded.status == BatchJobStatus.RUNNING
