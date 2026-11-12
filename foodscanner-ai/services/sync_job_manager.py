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
    """Thread-safe persistent manager for background synchronization jobs."""

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        batch_service: Optional[BatchIngestionService] = None,
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

        # Load persisted jobs on initialization
        self._load_all_jobs()

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

    def _persist_job(self, job: BatchJob) -> None:
        """Atomically persist job state to disk using tempfile + atomic rename."""
        file_path = self._get_job_file_path(job.job_id)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.storage_dir,
                delete=False,
            ) as tf:
                json.dump(job.to_dict(), tf, indent=2)
                temp_name = tf.name

            os.replace(temp_name, file_path)
        except Exception as exc:
            logger.error(f"Failed to persist batch job {job.job_id} to disk: {exc}")

    def create_job(
        self,
        barcodes: List[str],
        force_refresh: bool = False,
        prevent_duplicates: bool = True,
        original_job_id: Optional[str] = None,
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
            )
            self._jobs[job_id] = job
            self._persist_job(job)
            logger.info(f"Created batch job {job_id} with {len(barcodes)} barcode(s).")
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
        """Reap and recover jobs stranded in PENDING or RUNNING state after worker restart."""
        with self._lock:
            now = datetime.now(timezone.utc)
            reaped_count = 0

            for job in list(self._jobs.values()):
                if job.status in (BatchJobStatus.PENDING, BatchJobStatus.RUNNING):
                    ref_str = job.started_at or job.created_at
                    try:
                        ref_dt = datetime.fromisoformat(ref_str.replace("Z", "+00:00"))
                        if ref_dt.tzinfo is None:
                            ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                        age = (now - ref_dt).total_seconds()
                    except Exception:
                        age = timeout_seconds + 1

                    if age >= timeout_seconds:
                        logger.warning(
                            f"Reaping stale batch job {job.job_id} (status={job.status.value}, age={age:.1f}s)"
                        )
                        job.status = BatchJobStatus.FAILED
                        job.error_category = BatchErrorCategory.TIMEOUT_OR_PROCESS_RESTART.value
                        job.last_error = "Batch job timed out or worker process restarted. Eligible for retry."
                        job.completed_at = now.isoformat()
                        self._persist_job(job)
                        reaped_count += 1

            if reaped_count > 0:
                logger.info(f"Stale batch job sweep: reaped {reaped_count} abandoned job(s).")
            return reaped_count

    async def execute_job(self, job_id: str) -> BatchJob:
        """Execute a batch job asynchronously and persist progress and final results."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError(f"Batch job {job_id} not found.")

            if job.status == BatchJobStatus.RUNNING:
                logger.info(f"Job {job_id} is already running.")
                return job

            now_str = datetime.now(timezone.utc).isoformat()
            job.status = BatchJobStatus.RUNNING
            job.started_at = now_str
            self._persist_job(job)

        start_time = time.perf_counter()
        try:
            # Execute batch ingestion
            result: BatchIngestionResult = await self.batch_service.ingest_batch_async(
                barcodes=job.barcodes,
                force_refresh=job.force_refresh,
                batch_id=job.job_id,
            )

            with self._lock:
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

                self._persist_job(job)
                logger.info(f"Batch job {job_id} finished with status={job.status.value} in {duration:.2f}s")
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
                self._persist_job(job)
                return job

    def retry_job(
        self,
        job_id: str,
        force_refresh: Optional[bool] = None,
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

            retry_job = self.create_job(
                barcodes=barcodes_to_retry,
                force_refresh=eff_force,
                prevent_duplicates=False,
                original_job_id=job_id,
            )
            retry_job.attempt_count = original.attempt_count + 1
            self._persist_job(retry_job)
            logger.info(
                f"Created retry job {retry_job.job_id} (attempt {retry_job.attempt_count}) "
                f"for {len(barcodes_to_retry)} barcode(s) from original job {job_id}."
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
