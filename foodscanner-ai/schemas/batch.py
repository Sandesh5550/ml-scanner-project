from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BatchJobStatus(str, Enum):
    """Lifecycle status for asynchronous batch ingestion and background synchronization jobs."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchItemStatus(str, Enum):
    """Exhaustive outcome classification for individual barcode operations within a batch."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_FOUND = "NOT_FOUND"


class BatchActionTaken(str, Enum):
    """Detailed action classification for an ingested product."""
    FETCHED = "FETCHED"
    CACHED_FRESH = "CACHED_FRESH"
    MERGED = "MERGED"
    CSV_FALLBACK = "CSV_FALLBACK"
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE_IN_BATCH = "DUPLICATE_IN_BATCH"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    ERROR = "ERROR"


class BatchErrorCategory(str, Enum):
    """Normalized error classification for operational troubleshooting and retry gating."""
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    NOT_FOUND = "NOT_FOUND"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    PERMANENT_ERROR = "PERMANENT_ERROR"
    TIMEOUT_OR_PROCESS_RESTART = "TIMEOUT_OR_PROCESS_RESTART"


class BatchItemResult(BaseModel):
    """Structured per-item outcome for a barcode processed in a batch."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    barcode: str
    status: BatchItemStatus
    product_name: Optional[str] = None
    brand: Optional[str] = None
    action_taken: BatchActionTaken
    error_category: Optional[BatchErrorCategory] = None
    error_message: Optional[str] = None
    data_confidence: Optional[str] = None
    nutrition_completeness: Optional[float] = None
    is_fresh: bool = False
    retries_attempted: int = 0


class BatchIngestionResult(BaseModel):
    """Structured aggregate execution summary for a completed batch ingestion run."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str
    total_submitted: int
    valid_identifiers: int
    duplicate_identifiers: int
    successfully_processed: int
    failed: int
    skipped: int
    not_found: int
    duration_seconds: float
    created_at: str
    completed_at: str
    items: List[BatchItemResult] = Field(default_factory=list)


DEFAULT_MAX_BATCH_SIZE = 100


class BatchIngestRequest(BaseModel):
    """Request payload for batch product ingestion."""
    model_config = ConfigDict(extra="forbid")

    barcodes: List[str] = Field(
        ...,
        min_length=1,
        max_length=DEFAULT_MAX_BATCH_SIZE,
        description="List of product barcodes to ingest (1 to 100 items).",
    )
    force_refresh: bool = Field(
        default=False,
        description="If True, bypasses database cache freshness check and forces an OpenFoodFacts lookup.",
    )
    async_job: bool = Field(
        default=True,
        description="If True, returns an asynchronous job ID immediately; if False, runs synchronously.",
    )

    @field_validator("barcodes")
    @classmethod
    def validate_barcodes_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Barcodes list cannot be empty.")
        if len(v) > DEFAULT_MAX_BATCH_SIZE:
            raise ValueError(f"Batch size {len(v)} exceeds maximum allowed limit ({DEFAULT_MAX_BATCH_SIZE}).")
        return v


class BatchJobResponse(BaseModel):
    """Response payload for batch job status and observability."""
    model_config = ConfigDict(extra="ignore")

    job_id: str
    status: BatchJobStatus
    total_submitted: int
    processed_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    not_found_count: int = 0
    attempt_count: int = 1
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    last_error: Optional[str] = None
    error_category: Optional[str] = None
    status_url: Optional[str] = None
    items: Optional[List[BatchItemResult]] = None


class BatchRetryRequest(BaseModel):
    """Request payload for retrying failed items in a batch job."""
    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = Field(
        default=False,
        description="If True, forces an OpenFoodFacts refresh during retry.",
    )


# Rebuild models for OpenAPI and Pydantic v2 runtime introspection
BatchItemResult.model_rebuild()
BatchIngestionResult.model_rebuild()
BatchIngestRequest.model_rebuild()
BatchJobResponse.model_rebuild()
BatchRetryRequest.model_rebuild()
