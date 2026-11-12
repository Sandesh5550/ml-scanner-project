#!/usr/bin/env python3
"""Safe non-destructive background enrichment script for PRAMAAN catalog products.

Phase 8 — Batch Ingestion & Background Sync.

Usage:
    python scripts/enrich_incomplete_products.py --limit 50 --batch-size 10 --concurrency 2
    python scripts/enrich_incomplete_products.py --barcodes 8901030000000,8901491101837
    python scripts/enrich_incomplete_products.py --dry-run --limit 20
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
import sys
import time
from pathlib import Path

# Ensure foodscanner-ai directory is in Python path
AI_DIR = Path(__file__).resolve().parents[1]
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from database.db_session import SessionLocal
from database.models import Product
from schemas.batch import BatchActionTaken, BatchItemStatus
from services.batch_ingestion_service import BatchIngestionService
from services.config import load_environment
from services.db_service import get_enrichment_candidates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("enrich_incomplete_products")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PRAMAAN Safe Non-Destructive Product Enrichment Script (Phase 8)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of candidate products to enrich (default: 50, max: 500).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of products to process per batch (default: 10, max: 50).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Maximum concurrent network requests (default: 2, max: 5).",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Force an external OpenFoodFacts refresh even if existing record is fresh.",
    )
    parser.add_argument(
        "--stale-only",
        action="store_true",
        default=False,
        help="Only target products older than the 30-day cache TTL or missing fetch timestamp.",
    )
    parser.add_argument(
        "--incomplete-only",
        action="store_true",
        default=False,
        help="Only target products missing mandatory statutory nutrients (calories, sugar, fat, salt/sodium).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Inspect candidate products and display what would be enriched without modifying database.",
    )
    parser.add_argument(
        "--barcodes",
        type=str,
        default="",
        help="Comma-separated list of explicit barcodes to enrich (bypasses DB candidate search).",
    )
    return parser.parse_args()


def run_enrichment(args: argparse.Namespace) -> int:
    load_environment()
    start_time = time.perf_counter()

    clamped_limit = max(1, min(args.limit, 500))
    clamped_batch_size = max(1, min(args.batch_size, 50))
    clamped_concurrency = max(1, min(args.concurrency, 5))

    logger.info("=" * 70)
    logger.info("PRAMAAN CATALOG ENRICHMENT ENGINE (PHASE 8)")
    logger.info(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    logger.info(
        f"Settings: limit={clamped_limit}, batch_size={clamped_batch_size}, "
        f"concurrency={clamped_concurrency}, force_refresh={args.force_refresh}, "
        f"dry_run={args.dry_run}"
    )
    logger.info("=" * 70)

    # 1. Gather barcodes to enrich
    barcodes_to_enrich: list[str] = []

    if args.barcodes.strip():
        # User supplied explicit barcodes
        raw_codes = [b.strip() for b in args.barcodes.split(",") if b.strip()]
        for code in raw_codes:
            norm = BatchIngestionService.normalize_barcode(code)
            if norm:
                barcodes_to_enrich.append(norm)
        logger.info(f"Targeting {len(barcodes_to_enrich)} user-specified barcode(s).")
    else:
        # Query DB for enrichment candidates
        db = SessionLocal()
        try:
            candidates = get_enrichment_candidates(
                db=db,
                limit=clamped_limit,
                stale_only=args.stale_only,
                incomplete_only=args.incomplete_only,
            )
            barcodes_to_enrich = [p.barcode for p in candidates if p.barcode]
            logger.info(f"Found {len(barcodes_to_enrich)} candidate product(s) in local database needing enrichment.")
        finally:
            db.close()

    if not barcodes_to_enrich:
        logger.info("Zero products require enrichment. Database catalog is up-to-date.")
        return 0

    if args.dry_run:
        logger.info("DRY RUN: The following barcodes would be submitted for enrichment:")
        for idx, bc in enumerate(barcodes_to_enrich, 1):
            logger.info(f"  {idx}. Barcode: {bc}")
        logger.info(f"DRY RUN COMPLETE: {len(barcodes_to_enrich)} candidate(s) listed.")
        return 0

    # 2. Chunk into batches and process
    service = BatchIngestionService(
        max_concurrency=clamped_concurrency,
    )

    batches: list[list[str]] = [
        barcodes_to_enrich[i : i + clamped_batch_size]
        for i in range(0, len(barcodes_to_enrich), clamped_batch_size)
    ]

    total_submitted = len(barcodes_to_enrich)
    total_success = 0
    total_fetched = 0
    total_merged = 0
    total_cached_fresh = 0
    total_csv = 0
    total_not_found = 0
    total_failed = 0

    for batch_num, batch_codes in enumerate(batches, 1):
        logger.info(f"Processing Batch {batch_num}/{len(batches)} ({len(batch_codes)} barcodes)...")
        result = service.ingest_batch(
            barcodes=batch_codes,
            force_refresh=args.force_refresh,
        )

        for item in result.items:
            if item.status == BatchItemStatus.SUCCESS:
                total_success += 1
                if item.action_taken == BatchActionTaken.FETCHED:
                    total_fetched += 1
                elif item.action_taken == BatchActionTaken.MERGED:
                    total_merged += 1
                elif item.action_taken == BatchActionTaken.CACHED_FRESH:
                    total_cached_fresh += 1
                elif item.action_taken == BatchActionTaken.CSV_FALLBACK:
                    total_csv += 1
            elif item.status == BatchItemStatus.NOT_FOUND:
                total_not_found += 1
            elif item.status == BatchItemStatus.FAILED:
                total_failed += 1

    duration = time.perf_counter() - start_time
    logger.info("=" * 70)
    logger.info("ENRICHMENT EXECUTION SUMMARY")
    logger.info(f"Total Submitted:        {total_submitted}")
    logger.info(f"Successfully Resolved:  {total_success}")
    logger.info(f"  - Newly Fetched (OFF):{total_fetched}")
    logger.info(f"  - Merged with DB:     {total_merged}")
    logger.info(f"  - Already Fresh:      {total_cached_fresh}")
    logger.info(f"  - Curated CSV:        {total_csv}")
    logger.info(f"Not Found (OFF & CSV):  {total_not_found}")
    logger.info(f"Failed / Errored:       {total_failed}")
    logger.info(f"Execution Duration:     {duration:.2f} seconds")
    logger.info("=" * 70)

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_enrichment(parse_arguments()))
