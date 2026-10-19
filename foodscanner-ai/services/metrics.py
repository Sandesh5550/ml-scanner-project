from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional, Tuple

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# Dedicated registry to isolate from default global state and avoid duplicate-registration errors in tests
METRICS_REGISTRY = CollectorRegistry(auto_describe=True)
_lock = threading.Lock()

# 1. HTTP Traffic & Performance Metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests processed by endpoint and status code",
    ["method", "endpoint", "status_code"],
    registry=METRICS_REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=METRICS_REGISTRY,
)

# 2. OCR Subsystem Metrics
OCR_JOBS_TOTAL = Counter(
    "ocr_jobs_total",
    "Total OCR jobs submitted by execution mode",
    ["mode"],
    registry=METRICS_REGISTRY,
)

OCR_JOB_STATUS_TOTAL = Counter(
    "ocr_job_status_total",
    "Terminal status counts for OCR processing jobs",
    ["status"],
    registry=METRICS_REGISTRY,
)

OCR_JOB_DURATION_SECONDS = Histogram(
    "ocr_job_duration_seconds",
    "Duration of OCR inference and claim verification in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0),
    registry=METRICS_REGISTRY,
)

OCR_STALE_JOBS_REAPED_TOTAL = Counter(
    "ocr_stale_jobs_reaped_total",
    "Total count of stale OCR jobs reaped after worker timeout",
    registry=METRICS_REGISTRY,
)

OCR_CONFIDENCE_TOTAL = Counter(
    "ocr_confidence_total",
    "Distribution of qualitative OCR confidence grades",
    ["level"],
    registry=METRICS_REGISTRY,
)

# 3. Security & Rate-Limiting Metrics
RATE_LIMIT_EXCEEDED_TOTAL = Counter(
    "rate_limit_exceeded_total",
    "Total rate-limit rejections (HTTP 429) by endpoint group",
    ["endpoint"],
    registry=METRICS_REGISTRY,
)


def init_metrics() -> None:
    """Pre-initialize known, bounded label combinations to 0.0.

    Prometheus best practices dictate pre-initializing labeled counters to 0
    so that metrics exposition and Prometheus TSDB track the series from startup,
    preventing missing series / empty vector queries prior to the first event.
    """
    for mode in ("sync", "async"):
        OCR_JOBS_TOTAL.labels(mode=mode)

    for status in ("completed", "failed"):
        OCR_JOB_STATUS_TOTAL.labels(status=status)

    for level in ("high", "medium", "low"):
        OCR_CONFIDENCE_TOTAL.labels(level=level)

    for endpoint in ("auth", "ocr", "scan", "chat"):
        RATE_LIMIT_EXCEEDED_TOTAL.labels(endpoint=endpoint)


# Initialize bounded metric series on module load
init_metrics()


def normalize_metric_endpoint(raw_path: str, matched_route: Optional[str] = None) -> str:
    """Normalize request path into a bounded, template-based metric label.

    Prevents high-cardinality label explosion by replacing dynamic IDs, barcodes,
    and hashes with standardized placeholders.
    """
    if matched_route and matched_route.strip():
        return matched_route.strip()

    path = (raw_path or "").split("?")[0].rstrip("/") or "/"

    # Static routes
    if path in {
        "/",
        "/health",
        "/metrics",
        "/register",
        "/login",
        "/ocr",
        "/food-log",
        "/analyze",
        "/scan",
        "/search",
        "/autocomplete",
        "/compare",
        "/alternatives",
        "/verify-claims",
        "/today",
        "/stats",
        "/history",
        "/favorites",
        "/user/profile",
        "/report/daily",
        "/report/weekly",
        "/report/goal",
        "/chat",
    }:
        return path

    # Dynamic path pattern normalization
    if path.startswith("/ocr/jobs/"):
        return "/ocr/jobs/{job_id}"
    if path.startswith("/product/"):
        return "/product/{barcode}"
    if path.startswith("/alternatives/"):
        return "/alternatives/{barcode}"
    if path.startswith("/explain/"):
        return "/explain/{barcode}"
    if path.startswith("/food-log/"):
        return "/food-log/{log_id}"
    if path.startswith("/history/"):
        return "/history/{scan_id}"
    if path.startswith("/favorites/"):
        return "/favorites/{product_id}"

    # General dynamic segment sanitization
    sanitized = re.sub(r"/\d+", "/{id}", path)
    sanitized = re.sub(r"/[a-f0-9]{8,64}", "/{id}", sanitized)

    # Bound unrecognized paths to prevent cardinality leaks
    if len(sanitized) > 40:
        return "/unmatched"

    return sanitized


def record_http_request(
    method: str,
    endpoint: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record HTTP request throughput and latency."""
    safe_method = (method or "UNKNOWN").upper()
    safe_endpoint = endpoint or "/unmatched"
    safe_status = str(status_code)

    try:
        HTTP_REQUESTS_TOTAL.labels(
            method=safe_method,
            endpoint=safe_endpoint,
            status_code=safe_status,
        ).inc()

        if duration_seconds >= 0:
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=safe_method,
                endpoint=safe_endpoint,
            ).observe(duration_seconds)
    except Exception as exc:
        logger.debug("Error recording HTTP metrics: %s", exc)


def record_ocr_job_created(mode: str) -> None:
    """Increment OCR job creation counter by mode ('sync' or 'async')."""
    safe_mode = "async" if mode == "async" else "sync"
    try:
        OCR_JOBS_TOTAL.labels(mode=safe_mode).inc()
    except Exception as exc:
        logger.debug("Error recording OCR creation metric: %s", exc)


def record_ocr_job_status(status: str, duration_seconds: Optional[float] = None) -> None:
    """Record terminal status and optional processing duration for an OCR job."""
    safe_status = "completed" if status == "completed" else "failed"
    try:
        OCR_JOB_STATUS_TOTAL.labels(status=safe_status).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            OCR_JOB_DURATION_SECONDS.observe(duration_seconds)
    except Exception as exc:
        logger.debug("Error recording OCR status metric: %s", exc)


def record_ocr_stale_reaped(count: int = 1) -> None:
    """Record count of stale OCR jobs reaped."""
    if count <= 0:
        return
    try:
        OCR_STALE_JOBS_REAPED_TOTAL.inc(count)
    except Exception as exc:
        logger.debug("Error recording stale OCR reaped metric: %s", exc)


def record_ocr_confidence(level: Any) -> None:
    """Record qualitative OCR detection confidence."""
    if isinstance(level, (int, float)):
        if level >= 0.75:
            norm_level = "high"
        elif level >= 0.40:
            norm_level = "medium"
        else:
            norm_level = "low"
    else:
        norm_level = str(level or "low").strip().lower()
        if norm_level not in {"high", "medium", "low"}:
            norm_level = "low"
    try:
        OCR_CONFIDENCE_TOTAL.labels(level=norm_level).inc()
    except Exception as exc:
        logger.debug("Error recording OCR confidence metric: %s", exc)


def record_rate_limit_exceeded(endpoint: str) -> None:
    """Record rate limit rejection event (HTTP 429)."""
    safe_endpoint = (endpoint or "unknown").strip().lower()
    try:
        RATE_LIMIT_EXCEEDED_TOTAL.labels(endpoint=safe_endpoint).inc()
    except Exception as exc:
        logger.debug("Error recording rate limit metric: %s", exc)


def get_metrics_exposition() -> Tuple[bytes, str]:
    """Generate Prometheus exposition format payload from the dedicated registry."""
    return generate_latest(METRICS_REGISTRY), CONTENT_TYPE_LATEST
