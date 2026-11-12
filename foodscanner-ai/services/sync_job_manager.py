from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

from schemas.batch import (
    BatchActionTaken,
    BatchErrorCategory,
    BatchIngestionResult,
    BatchItemResult,
    BatchItemStatus,
    BatchJobResponse,
    BatchJobStatus,
)
from services.batch_ingestion_service import (
    BatchIngestionService,
    get_default_batch_ingestion_service,
)

logger = logging.getLogger(__name__)

DEFAULT_JOB_TIMEOUT_SECONDS = 600  # 10 minutes

# Atomic Lua compare-and-delete: release lock only if the caller's ownership token matches
REDIS_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Atomic Lua compare-and-expire: extend lock TTL only if the caller's ownership token matches
REDIS_RENEW_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], tonumber(ARGV[2]))
else
    return 0
end
"""


class LockLostError(Exception):
    """Raised when distributed lock ownership is confirmed lost or expired during execution."""
    pass


def release_redis_lock(client: Any, lock_key: str, lock_token: str) -> bool:
    """Atomically release Redis lock only if the caller's ownership token matches."""
    if client is None or not lock_key or not lock_token:
        return False
    try:
        res = client.eval(REDIS_RELEASE_LOCK_LUA, 1, lock_key, str(lock_token))
        return bool(res)
    except Exception as exc:
        logger.warning(f"Failed to release Redis lock {lock_key} safely: {exc}")
        return False


def check_redis_lock_owner(client: Any, lock_key: str, lock_token: str) -> bool:
    """Verify that caller currently holds ownership of the Redis lock."""
    if client is None or not lock_key or not lock_token:
        return False
    try:
        current_val = client.get(lock_key)
        return current_val == str(lock_token)
    except Exception:
        return False


def renew_redis_lock_detailed(client: Any, lock_key: str, lock_token: str, ttl_seconds: int) -> tuple[bool, str]:
    """Atomically extend Redis lock TTL only if caller owns the lock.

    Returns:
        (True, "OK"): TTL successfully extended.
        (False, "LOST"): Redis reached, but lock belongs to another worker or expired (confirmed lost).
        (False, "ERROR"): Communication error with Redis (unconfirmed).
    """
    if client is None or not lock_key or not lock_token:
        return False, "ERROR"
    try:
        res = client.eval(REDIS_RENEW_LOCK_LUA, 1, lock_key, str(lock_token), str(ttl_seconds))
        if bool(res):
            return True, "OK"
        return False, "LOST"
    except Exception as exc:
        logger.warning(f"Failed to communicate with Redis for lock renewal on {lock_key}: {exc}")
        return False, "ERROR"


def renew_redis_lock(client: Any, lock_key: str, lock_token: str, ttl_seconds: int) -> bool:
    """Atomically extend Redis lock TTL only if caller owns the lock."""
    success, _ = renew_redis_lock_detailed(client, lock_key, lock_token, ttl_seconds)
    return success


class DuplicateJobError(Exception):
    """Raised when an identical batch is submitted while an active job is already running."""
    pass


@dataclass
class BatchJob:
    """Internal model for tracking persistent batch ingestion and background sync jobs."""
    job_id: str
    status: BatchJobStatus
    barcodes: List[str]
    force_refresh: bool
    total_submitted: int
    processed_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    not_found_count: int = 0
    attempt_count: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    error_category: Optional[str] = None
    failed_barcodes: List[str] = field(default_factory=list)
    items: List[BatchItemResult] = field(default_factory=list)
    original_job_id: Optional[str] = None
    user_id: Optional[int] = None
    execution_token: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value if isinstance(self.status, BatchJobStatus) else str(self.status),
            "barcodes": self.barcodes,
            "force_refresh": self.force_refresh,
            "total_submitted": self.total_submitted,
            "processed_count": self.processed_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "skipped_count": self.skipped_count,
            "not_found_count": self.not_found_count,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "last_error": self.last_error,
            "error_category": self.error_category,
            "failed_barcodes": self.failed_barcodes,
            "original_job_id": self.original_job_id,
            "user_id": self.user_id,
            "execution_token": self.execution_token,
            "items": [item.model_dump() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BatchJob:
        raw_items = data.get("items") or []
        parsed_items = [
            BatchItemResult.model_validate(it) if isinstance(it, dict) else it
            for it in raw_items
        ]
        raw_status = data.get("status", BatchJobStatus.PENDING.value)
        try:
            status_enum = BatchJobStatus(raw_status)
        except ValueError:
            status_enum = BatchJobStatus.FAILED

        return cls(
            job_id=data["job_id"],
            status=status_enum,
            barcodes=data.get("barcodes") or [],
            force_refresh=bool(data.get("force_refresh", False)),
            total_submitted=data.get("total_submitted", len(data.get("barcodes") or [])),
            processed_count=data.get("processed_count", 0),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            skipped_count=data.get("skipped_count", 0),
            not_found_count=data.get("not_found_count", 0),
            attempt_count=data.get("attempt_count", 1),
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            duration_seconds=data.get("duration_seconds"),
            last_error=data.get("last_error"),
            error_category=data.get("error_category"),
            failed_barcodes=data.get("failed_barcodes") or [],
            items=parsed_items,
            original_job_id=data.get("original_job_id"),
            user_id=data.get("user_id"),
            execution_token=data.get("execution_token"),
        )

    def to_response(self, base_url: str = "") -> BatchJobResponse:
        status_url = f"{base_url.rstrip('/')}/batch/jobs/{self.job_id}" if base_url or not base_url.startswith("/") else f"/batch/jobs/{self.job_id}"
        return BatchJobResponse(
            job_id=self.job_id,
            status=self.status,
            total_submitted=self.total_submitted,
            processed_count=self.processed_count,
            success_count=self.success_count,
            failure_count=self.failure_count,
            skipped_count=self.skipped_count,
            not_found_count=self.not_found_count,
            attempt_count=self.attempt_count,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_seconds=self.duration_seconds,
            last_error=self.last_error,
            error_category=self.error_category,
            status_url=status_url,
            items=self.items if self.status in (BatchJobStatus.COMPLETED, BatchJobStatus.FAILED) else None,
        )


class BatchJobManager:
    """Thread-safe persistent manager for background synchronization jobs.

    DEPLOYMENT & STORAGE CONCURRENCY ARCHITECTURE:
    1. Single-Instance / Multi-Worker Topology:
       Workers coordinate job state via the local filesystem (`BATCH_JOB_STORAGE_DIR`)
       and enforce single-worker-per-job execution via atomic POSIX file locks (`{job_id}.lock`).
    2. Horizontal Multi-Replica Clustered Topology (Kubernetes / ECS / Docker replicas):
       Replicas coordinate execution via distributed Redis locks (`lock:batch:{job_id}`).
       NOTE: For full multi-replica persistence, `BATCH_JOB_STORAGE_DIR` MUST be mounted on
       shared durable network storage (e.g. AWS EFS / NFS), or state must be backed by a
       central database. Independent ephemeral container filesystems will not observe each
       other's local disk files.
    """

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        batch_service: Optional[BatchIngestionService] = None,
        redis_client: Optional[Any] = None,
    ) -> None:
        if storage_dir is None:
            raw_dir = os.environ.get("BATCH_JOB_STORAGE_DIR")
            if raw_dir and str(raw_dir).strip():
                storage_dir = Path(raw_dir).resolve()
            else:
                base_ai = Path(__file__).resolve().parents[1]
                storage_dir = base_ai / "data" / "batch_jobs"

        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.batch_service = batch_service or get_default_batch_ingestion_service()
        self._lock = threading.RLock()
        self._jobs: Dict[str, BatchJob] = {}
        self._shutdown_event = threading.Event()
        self._redis_client = redis_client

        # Load persisted jobs on initialization
        self._load_all_jobs()

    def set_redis_client(self, client: Optional[Any]) -> None:
        """Inject or clear shared Redis client for distributed locking."""
        with self._lock:
            self._redis_client = client

    @staticmethod
    def release_redis_lock(client: Any, lock_key: str, lock_token: str) -> bool:
        """Atomically release Redis lock only if caller owns the token."""
        return release_redis_lock(client, lock_key, lock_token)

    @staticmethod
    def check_redis_lock_owner(client: Any, lock_key: str, lock_token: str) -> bool:
        """Verify that caller currently holds ownership of the Redis lock."""
        return check_redis_lock_owner(client, lock_key, lock_token)

    @staticmethod
    def renew_redis_lock(client: Any, lock_key: str, lock_token: str, ttl_seconds: int) -> bool:
        """Atomically extend Redis lock TTL only if caller owns the token."""
        return renew_redis_lock(client, lock_key, lock_token, ttl_seconds)

    @classmethod
    async def _lock_renew_loop(
        cls,
        client: Any,
        lock_key: str,
        lock_token: str,
        ttl_seconds: int,
        interval_seconds: float,
        stop_event: asyncio.Event,
        abort_event: asyncio.Event,
        abort_info: dict[str, Any],
    ) -> None:
        """Background coroutine extending Redis lock TTL periodically with fail-closed safety.

        Guarantees:
        1. If Redis confirms ownership is LOST, abort_event is set IMMEDIATELY.
        2. If Redis is temporarily unavailable (ERROR), retries rapidly until:
           - Redis recovers (advancing the safety deadline), OR
           - Ownership is confirmed LOST, OR
           - Current monotonic time reaches the safety deadline (fail closed).
        """
        last_renewed_ts = time.monotonic()
        retry_interval = max(0.05, min(2.0, interval_seconds / 5.0))
        safety_margin = max(0.5, min(10.0, ttl_seconds * 0.15))

        while not stop_event.is_set():
            now = time.monotonic()
            safe_deadline = last_renewed_ts + ttl_seconds - safety_margin
            if now >= safe_deadline:
                logger.error(
                    f"Distributed lock safety deadline exceeded for {lock_key} (now={now:.2f}, deadline={safe_deadline:.2f}). Aborting execution."
                )
                abort_info["reason"] = "SAFETY_DEADLINE_EXCEEDED"
                abort_event.set()
                break

            time_to_wait = min(interval_seconds, max(0.05, safe_deadline - now))
            try:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=time_to_wait)
                    break
                except asyncio.TimeoutError:
                    pass

                if stop_event.is_set():
                    break

                success, status = renew_redis_lock_detailed(client, lock_key, lock_token, ttl_seconds)
                if success:
                    last_renewed_ts = time.monotonic()
                    continue

                if status == "LOST":
                    logger.error(
                        f"Distributed lock ownership CONFIRMED LOST for {lock_key}. Immediate abort triggered."
                    )
                    abort_info["reason"] = "LOCK_CONFIRMED_LOST"
                    abort_event.set()
                    break

                if status == "ERROR":
                    now = time.monotonic()
                    if now >= (last_renewed_ts + ttl_seconds - safety_margin):
                        logger.error(
                            f"Redis unreachable and lock safety deadline reached for {lock_key}. Fail closed."
                        )
                        abort_info["reason"] = "REDIS_UNREACHABLE_DEADLINE_EXPIRED"
                        abort_event.set()
                        break

                    logger.warning(
                        f"Temporary Redis failure during lock renewal for {lock_key}; retrying within safe window (remaining: {last_renewed_ts + ttl_seconds - safety_margin - now:.1f}s)."
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=retry_interval)
                        break
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Unexpected error in lock renewal loop for {lock_key}: {exc}")
                abort_info["reason"] = f"UNEXPECTED_RENEWAL_ERROR: {exc}"
                abort_event.set()
                break

    def _get_job_file_path(self, job_id: str) -> Path:
        safe_id = "".join(c for c in job_id if c.isalnum() or c in ("-", "_"))
        return self.storage_dir / f"{safe_id}.json"

    def _load_all_jobs(self) -> None:
        """Scan storage directory and hydrate in-memory job cache."""
        with self._lock:
            try:
                for file_path in self.storage_dir.glob("*.json"):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        job = BatchJob.from_dict(data)
                        self._jobs[job.job_id] = job
                    except Exception as exc:
                        logger.warning(f"Failed to load job file {file_path.name}: {exc}")
                logger.info(f"BatchJobManager loaded {len(self._jobs)} job(s) from {self.storage_dir}")
            except Exception as exc:
                logger.warning(f"Error accessing batch job storage directory {self.storage_dir}: {exc}")

    def _persist_job(self, job: BatchJob, enforce_token: bool = True) -> bool:
        """Atomically persist job state to disk with execution-token ownership fencing and inter-process locking.

        Returns:
            True if persisted successfully; False if rejected due to ownership conflict or error.
        """
        file_path = self._get_job_file_path(job.job_id)
        safe_id = "".join(c for c in job.job_id if c.isalnum() or c in ("-", "_"))
        persist_lock_path = self.storage_dir / f"{safe_id}.persist.lock"

        try:
            lock_fd = os.open(str(persist_lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)

                # Validate execution ownership against disk state if file exists
                if file_path.exists():
                    try:
                        with open(file_path, "r", encoding="utf-8") as existing_f:
                            on_disk_data = json.load(existing_f)
                        on_disk_token = on_disk_data.get("execution_token")
                        on_disk_status = on_disk_data.get("status")

                        if enforce_token:
                            # 1. If on disk an execution token is already present:
                            if on_disk_token is not None:
                                # Legacy or unowned caller (no token) cannot overwrite an owned job
                                if not job.execution_token:
                                    logger.warning(
                                        f"Suppressing unowned overwrite of job {job.job_id}: on disk owned by {on_disk_token}."
                                    )
                                    return False
                                # Caller with a different execution token cannot overwrite
                                if job.execution_token != on_disk_token:
                                    logger.warning(
                                        f"Suppressing overwrite of job {job.job_id}: on disk owned by {on_disk_token}, caller has {job.execution_token}."
                                    )
                                    return False
                            else:
                                # On disk has NO execution token (e.g. legacy record or newly created PENDING job).
                                # Terminal states on disk are protected from backward transition:
                                if on_disk_status in (BatchJobStatus.COMPLETED.value, BatchJobStatus.FAILED.value):
                                    if job.status == BatchJobStatus.FAILED and on_disk_status == BatchJobStatus.COMPLETED.value:
                                        logger.warning(
                                            f"Suppressing overwrite of completed job {job.job_id} with failed state."
                                        )
                                        return False
                    except Exception as read_err:
                        logger.warning(f"Error reading existing job file {job.job_id} during persist: {read_err}")

                # Atomic write via tempfile in same filesystem directory + os.replace
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.storage_dir,
                    delete=False,
                ) as tf:
                    json.dump(job.to_dict(), tf, indent=2)
                    temp_name = tf.name

                os.replace(temp_name, file_path)
                return True
            finally:
                if fcntl:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                try:
                    os.close(lock_fd)
                except Exception:
                    pass
        except Exception as exc:
            logger.error(f"Failed to persist batch job {job.job_id} to disk: {exc}")
            return False

    def create_job(
        self,
        barcodes: List[str],
        force_refresh: bool = False,
        prevent_duplicates: bool = True,
        original_job_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> BatchJob:
        """Create and persist a new batch ingestion job."""
        with self._lock:
            # 1. Duplicate active job check
            if prevent_duplicates:
                sorted_codes = sorted(list(set(str(b).strip() for b in barcodes if str(b).strip())))
                for existing in self._jobs.values():
                    if existing.status in (BatchJobStatus.PENDING, BatchJobStatus.RUNNING):
                        existing_sorted = sorted(list(set(str(b).strip() for b in existing.barcodes if str(b).strip())))
                        if sorted_codes == existing_sorted and existing.force_refresh == force_refresh:
                            # If an existing job belongs to same user (or unspecified), reuse it
                            if existing.user_id == user_id or user_id is None:
                                logger.info(f"Returning existing active job {existing.job_id} for identical batch.")
                                return existing

            job_id = f"job_{uuid.uuid4().hex[:16]}"
            job = BatchJob(
                job_id=job_id,
                status=BatchJobStatus.PENDING,
                barcodes=barcodes,
                force_refresh=force_refresh,
                total_submitted=len(barcodes),
                original_job_id=original_job_id,
                user_id=user_id,
            )
            self._jobs[job_id] = job
            self._persist_job(job)
            logger.info(f"Created batch job {job_id} with {len(barcodes)} barcode(s) for user {user_id}.")
            return job

    def get_job(self, job_id: str) -> Optional[BatchJob]:
        """Retrieve a job by ID from memory cache or disk."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return job

        # Try disk lookup
        file_path = self._get_job_file_path(job_id)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                job = BatchJob.from_dict(data)
                with self._lock:
                    self._jobs[job_id] = job
                return job
            except Exception as exc:
                logger.warning(f"Error reading job file {job_id}: {exc}")
        return None

    def recover_stale_jobs(self, timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS) -> int:
        """Reap and recover jobs stranded in PENDING or RUNNING state after worker restart with distributed safety.

        Guarantees:
        1. Never reaps a RUNNING job if its distributed Redis lock is actively held/renewed.
        2. Never reaps a RUNNING job on a local node if its POSIX process lock is actively held.
        3. Coordinates stale recovery via an atomic Redis recovery lock ('recovery_{uuid}') with SET NX EX.
        4. If a live worker or another reaper holds the lock, backs off without modifying state.
        5. If Redis is unreachable/disconnected, fails closed without modifying distributed state.
        6. Persists reaped state using the recovery token, preventing races with concurrent workers.
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            reaped_count = 0

            for job in list(self._jobs.values()):
                if job.status not in (BatchJobStatus.PENDING, BatchJobStatus.RUNNING):
                    continue

                redis_lock_key = f"lock:batch:{job.job_id}"
                recovery_token = f"recovery_{uuid.uuid4().hex}"
                redis_recovery_acquired = False

                # 1. Distributed coordination via Redis
                if self._redis_client is not None:
                    try:
                        # Attempt to claim the recovery lock atomically
                        acquired = self._redis_client.set(
                            redis_lock_key,
                            recovery_token,
                            nx=True,
                            ex=60,  # 60-second recovery lock window
                        )
                        if not acquired:
                            # Lock is actively held by a worker renewing it, or another reaper
                            logger.info(
                                f"Stale sweep: Job {job.job_id} has an active Redis lock. Preserving active job."
                            )
                            continue
                        redis_recovery_acquired = True
                    except Exception as r_err:
                        # Redis unreachable: fail closed conservatively
                        logger.warning(
                            f"Stale sweep: Redis communication error checking job {job.job_id} ({r_err}). Skipping distributed recovery to prevent split-brain."
                        )
                        continue

                # 2. Local process coordination via POSIX file lock
                lock_file = self.storage_dir / f"{job.job_id}.lock"
                local_locked = False
                if lock_file.exists():
                    try:
                        fd = os.open(str(lock_file), os.O_RDWR)
                        try:
                            if fcntl:
                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                # Successfully locked -> old process is definitely dead
                                fcntl.flock(fd, fcntl.LOCK_UN)
                        except (BlockingIOError, IOError):
                            # Actively locked by a running local process!
                            local_locked = True
                        finally:
                            os.close(fd)
                    except Exception:
                        pass

                if local_locked:
                    logger.info(f"Stale sweep: Job {job.job_id} is held by active local process lock. Preserving.")
                    if redis_recovery_acquired and self._redis_client is not None:
                        release_redis_lock(self._redis_client, redis_lock_key, recovery_token)
                    continue

                # 3. Check age
                ref_str = job.started_at or job.created_at
                try:
                    ref_dt = datetime.fromisoformat(ref_str.replace("Z", "+00:00"))
                    if ref_dt.tzinfo is None:
                        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                    age = (now - ref_dt).total_seconds()
                except Exception:
                    age = timeout_seconds + 1

                if age < timeout_seconds:
                    if redis_recovery_acquired and self._redis_client is not None:
                        release_redis_lock(self._redis_client, redis_lock_key, recovery_token)
                    continue

                # 4. Reap stale job safely
                logger.warning(
                    f"Reaping stale batch job {job.job_id} (status={job.status.value}, age={age:.1f}s)"
                )
                job.status = BatchJobStatus.FAILED
                job.error_category = BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value
                job.last_error = "Batch job timed out or worker process restarted. Eligible for retry."
                job.completed_at = now.isoformat()
                job.execution_token = recovery_token

                # Persist with recovery_token
                persisted = self._persist_job(job, enforce_token=False)
                if persisted:
                    reaped_count += 1

                # 5. Cleanup
                try:
                    if lock_file.exists():
                        lock_file.unlink()
                except Exception:
                    pass

                if redis_recovery_acquired and self._redis_client is not None:
                    try:
                        release_redis_lock(self._redis_client, redis_lock_key, recovery_token)
                    except Exception:
                        pass

            if reaped_count > 0:
                logger.info(f"Stale batch job sweep: reaped {reaped_count} abandoned job(s).")
            return reaped_count

    async def execute_job(self, job_id: str) -> BatchJob:
        """Execute a batch job asynchronously with atomic single-worker process locking and fail-closed renewal."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError(f"Batch job {job_id} not found.")

            if job.status == BatchJobStatus.RUNNING:
                logger.info(f"Job {job_id} is already running.")
                return job

        # 1. Distributed Redis Lock (for multi-replica / multi-container coordination)
        redis_lock_key = f"lock:batch:{job_id}"
        redis_lock_acquired = False
        lock_token = uuid.uuid4().hex
        renew_stop_event = asyncio.Event()
        abort_event = asyncio.Event()
        abort_info: dict[str, Any] = {}
        renew_task: Optional[asyncio.Task] = None

        if self._redis_client is not None:
            try:
                acquired = self._redis_client.set(
                    redis_lock_key,
                    lock_token,
                    nx=True,
                    ex=DEFAULT_JOB_TIMEOUT_SECONDS,
                )
                if not acquired:
                    logger.info(
                        f"Batch job {job_id} is already locked in Redis by another container/worker. Skipping redundant start."
                    )
                    job = self.get_job(job_id)
                    if job:
                        return job
                    raise ValueError(f"Batch job {job_id} not found.")
                redis_lock_acquired = True

                # Start background renewal for long-running batches with fail-closed safety
                raw_interval = os.environ.get("BATCH_LOCK_RENEW_INTERVAL")
                if raw_interval and str(raw_interval).strip():
                    renew_interval = max(0.02, float(raw_interval))
                else:
                    renew_interval = max(0.5, min(60.0, DEFAULT_JOB_TIMEOUT_SECONDS / 3.0))
                renew_task = asyncio.create_task(
                    self._lock_renew_loop(
                        self._redis_client,
                        redis_lock_key,
                        lock_token,
                        DEFAULT_JOB_TIMEOUT_SECONDS,
                        renew_interval,
                        renew_stop_event,
                        abort_event,
                        abort_info,
                    )
                )
            except Exception as r_err:
                logger.warning(f"Could not acquire Redis lock for batch job {job_id}: {r_err}")

        # 2. Local Atomic File Lock (for multi-worker / process coordination on shared storage)
        lock_file = self.storage_dir / f"{job_id}.lock"
        lock_fd: Optional[int] = None
        lock_acquired = False
        try:
            lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            lock_acquired = True
            os.write(lock_fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode("utf-8"))
        except FileExistsError:
            logger.info(f"Batch job {job_id} is already being executed by another process/worker. Skipping redundant start.")
            if renew_task is not None:
                renew_stop_event.set()
                renew_task.cancel()
                try:
                    await renew_task
                except (asyncio.CancelledError, Exception):
                    pass
            if redis_lock_acquired and self._redis_client is not None:
                self.release_redis_lock(self._redis_client, redis_lock_key, lock_token)
            job = self.get_job(job_id)
            if job:
                return job
            raise ValueError(f"Batch job {job_id} not found.")
        except Exception as exc:
            logger.warning(f"Could not acquire file lock for batch job {job_id}: {exc}")

        with self._lock:
            now_str = datetime.now(timezone.utc).isoformat()
            job.status = BatchJobStatus.RUNNING
            job.started_at = now_str
            job.execution_token = lock_token
            persisted = self._persist_job(job)
            if not persisted:
                logger.error(
                    f"Batch job {job_id} could not be claimed on disk (execution token conflict). Aborting start."
                )
                if renew_task is not None:
                    renew_stop_event.set()
                    renew_task.cancel()
                    try:
                        await renew_task
                    except (asyncio.CancelledError, Exception):
                        pass
                if redis_lock_acquired and self._redis_client is not None:
                    self.release_redis_lock(self._redis_client, redis_lock_key, lock_token)
                if lock_acquired and lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except Exception:
                        pass
                    try:
                        if lock_file.exists():
                            lock_file.unlink()
                    except Exception:
                        pass
                job = self.get_job(job_id)
                if job:
                    return job
                raise ValueError(f"Batch job {job_id} could not be started due to disk ownership conflict.")

        start_time = time.perf_counter()
        try:
            # Execute batch ingestion with concurrent lock loss monitoring
            ingest_coro = self.batch_service.ingest_batch_async(
                barcodes=job.barcodes,
                force_refresh=job.force_refresh,
                batch_id=job.job_id,
            )
            ingest_task = asyncio.create_task(ingest_coro)
            abort_wait_task = asyncio.create_task(abort_event.wait())

            done, pending = await asyncio.wait(
                [ingest_task, abort_wait_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for p in pending:
                p.cancel()
                try:
                    await p
                except (asyncio.CancelledError, Exception):
                    pass

            if abort_event.is_set():
                reason = abort_info.get("reason", "LOCK_LOST")
                raise LockLostError(
                    f"Batch job {job_id} aborted: distributed lock was lost or expired ({reason})."
                )

            result: BatchIngestionResult = ingest_task.result()

            with self._lock:
                # Post-execution ownership verification guard:
                # Ensure lock was not lost in the narrow window before writing results
                if redis_lock_acquired and self._redis_client is not None:
                    is_owner = check_redis_lock_owner(self._redis_client, redis_lock_key, lock_token)
                    if not is_owner:
                        logger.error(
                            f"Post-execution lock check failed for job {job_id}: worker no longer owns Redis lock. Aborting save."
                        )
                        raise LockLostError(
                            f"Batch job {job_id} completed processing but Redis lock was lost prior to saving results."
                        )

                duration = time.perf_counter() - start_time
                now_str = datetime.now(timezone.utc).isoformat()

                job.processed_count = len(result.items)
                job.success_count = result.successfully_processed
                job.failure_count = result.failed
                job.skipped_count = result.skipped
                job.not_found_count = result.not_found
                job.items = result.items
                job.duration_seconds = round(duration, 3)
                job.completed_at = now_str
                job.execution_token = lock_token

                # Extract failed barcodes eligible for retry
                failed_codes = [
                    item.barcode
                    for item in result.items
                    if item.status in (BatchItemStatus.FAILED, BatchItemStatus.NOT_FOUND)
                ]
                job.failed_barcodes = failed_codes

                # If all items failed and none succeeded, mark as FAILED
                if result.successfully_processed == 0 and (result.failed > 0 or result.not_found > 0):
                    job.status = BatchJobStatus.FAILED
                    job.error_category = BatchErrorCategory.PERMANENT_ERROR.value
                    job.last_error = "All barcodes in batch failed to ingest or were not found."
                else:
                    job.status = BatchJobStatus.COMPLETED

                persisted = self._persist_job(job)
                if not persisted:
                    logger.error(
                        f"Post-execution disk write failed for job {job_id}: disk state owned by another execution token."
                    )
                    raise LockLostError(
                        f"Batch job {job_id} completed processing but disk state was claimed by another execution."
                    )

                try:
                    from services.metrics import record_batch_job_status
                    record_batch_job_status(job.status.value, duration)
                except Exception:
                    pass
                logger.info(f"Batch job {job_id} finished with status={job.status.value} in {duration:.2f}s")
                return job

        except LockLostError as exc:
            logger.error(f"Execution aborted for batch job {job_id} due to lock loss: {exc}")
            with self._lock:
                duration = time.perf_counter() - start_time
                now_str = datetime.now(timezone.utc).isoformat()
                job.status = BatchJobStatus.FAILED
                job.error_category = BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value
                job.last_error = str(exc)
                job.completed_at = now_str
                job.duration_seconds = round(duration, 3)
                job.execution_token = lock_token

                # Guard against overwriting a newer worker's state on disk
                persisted = self._persist_job(job)
                if not persisted:
                    logger.warning(
                        f"Disk job {job_id} overwrite suppressed: on-disk state is owned by another execution token."
                    )

                try:
                    from services.metrics import record_batch_job_status
                    record_batch_job_status("failed", duration)
                except Exception:
                    pass
                return job

        except Exception as exc:
            logger.exception(f"Fatal error while executing batch job {job_id}: {exc}")
            with self._lock:
                duration = time.perf_counter() - start_time
                now_str = datetime.now(timezone.utc).isoformat()
                job.status = BatchJobStatus.FAILED
                job.error_category = BatchErrorCategory.SERVER_ERROR.value
                job.last_error = f"Unhandled execution error: {exc}"
                job.completed_at = now_str
                job.duration_seconds = round(duration, 3)
                job.execution_token = lock_token

                persisted = self._persist_job(job)
                if not persisted:
                    logger.warning(
                        f"Disk job {job_id} fatal error overwrite suppressed: on-disk state is owned by another execution token."
                    )

                try:
                    from services.metrics import record_batch_job_status
                    record_batch_job_status("failed", duration)
                except Exception:
                    pass
                return job
        finally:
            if renew_task is not None:
                renew_stop_event.set()
                renew_task.cancel()
                try:
                    await renew_task
                except (asyncio.CancelledError, Exception):
                    pass
            if redis_lock_acquired and self._redis_client is not None:
                try:
                    self.release_redis_lock(self._redis_client, redis_lock_key, lock_token)
                except Exception as r_del_err:
                    logger.warning(f"Failed to release Redis lock {redis_lock_key}: {r_del_err}")
            if lock_acquired and lock_fd is not None:
                try:
                    os.close(lock_fd)
                except Exception:
                    pass
                try:
                    if lock_file.exists():
                        lock_file.unlink()
                except Exception:
                    pass

    def retry_job(
        self,
        job_id: str,
        force_refresh: Optional[bool] = None,
        user_id: Optional[int] = None,
    ) -> BatchJob:
        """Create a new retry job containing only the failed/unresolved barcodes from a previous run."""
        with self._lock:
            original = self.get_job(job_id)
            if original is None:
                raise ValueError(f"Job '{job_id}' does not exist.")

            if original.status in (BatchJobStatus.PENDING, BatchJobStatus.RUNNING):
                raise ValueError(f"Job '{job_id}' is currently active and cannot be retried.")

            barcodes_to_retry = original.failed_barcodes
            if not barcodes_to_retry:
                # Check items directly if failed_barcodes was empty
                barcodes_to_retry = [
                    it.barcode
                    for it in original.items
                    if it.status in (BatchItemStatus.FAILED, BatchItemStatus.NOT_FOUND)
                ]

            if not barcodes_to_retry:
                raise ValueError(f"Job '{job_id}' has zero failed or unassessable barcodes eligible for retry.")

            eff_force = force_refresh if force_refresh is not None else original.force_refresh
            eff_user_id = user_id if user_id is not None else original.user_id

            retry_job = self.create_job(
                barcodes=barcodes_to_retry,
                force_refresh=eff_force,
                prevent_duplicates=False,
                original_job_id=job_id,
                user_id=eff_user_id,
            )
            retry_job.attempt_count = original.attempt_count + 1
            self._persist_job(retry_job)
            logger.info(
                f"Created retry job {retry_job.job_id} (attempt {retry_job.attempt_count}) "
                f"for {len(barcodes_to_retry)} barcode(s) from original job {job_id} (user {eff_user_id})."
            )
            return retry_job

    def shutdown(self) -> None:
        """Signal worker shutdown and persist all in-memory jobs."""
        logger.info("BatchJobManager shutting down...")
        self._shutdown_event.set()
        with self._lock:
            for job in self._jobs.values():
                try:
                    self._persist_job(job)
                except Exception:
                    pass


_DEFAULT_JOB_MANAGER: Optional[BatchJobManager] = None


def get_default_job_manager() -> BatchJobManager:
    """Retrieve or initialize the thread-safe global BatchJobManager instance."""
    global _DEFAULT_JOB_MANAGER
    if _DEFAULT_JOB_MANAGER is None:
        _DEFAULT_JOB_MANAGER = BatchJobManager()
    return _DEFAULT_JOB_MANAGER


def reset_default_job_manager() -> None:
    """Reset the singleton instance for clean test isolation."""
    global _DEFAULT_JOB_MANAGER
    if _DEFAULT_JOB_MANAGER is not None:
        _DEFAULT_JOB_MANAGER.shutdown()
    _DEFAULT_JOB_MANAGER = None
