#!/usr/bin/env python3
"""PRAMAAN Database Migration Utility: SQLite -> PostgreSQL.

Safely transfers existing rows from a local SQLite database into a target
PostgreSQL instance while preserving IDs, foreign keys, constraints, and sequences.

Flow:
    SQLite (Read-only)
        ↓
    Read existing rows
        ↓
    Validate foreign keys & types
        ↓
    Insert into PostgreSQL (Parameterized, Atomic Transaction)
        ↓
    Reset PostgreSQL sequences
        ↓
    Verify row counts
        ↓
    Print migration summary report
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

# Ensure foodscanner-ai is on python path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database.orm import resolve_database_url
from services.config import redact_database_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_data")

MIGRATION_TABLES = [
    "users",
    "products",
    "nutrition",
    "scan_history",
    "daily_food_log",
]


def get_sqlite_engine(sqlite_path: Path) -> Engine:
    """Create a read-only SQLite engine for data extraction."""
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Source SQLite database not found: {sqlite_path}")
    db_url = f"sqlite:///{sqlite_path.resolve().as_posix()}"
    return create_engine(db_url, connect_args={"check_same_thread": False})


def get_target_engine(pg_url: str) -> Engine:
    """Create destination engine from resolved URL."""
    resolved_url = resolve_database_url(pg_url)
    return create_engine(resolved_url, pool_pre_ping=True)


def extract_table_rows(source_conn: Connection, table_name: str) -> List[Dict[str, Any]]:
    """Read all rows from source table in a dialect-neutral manner."""
    inspector = inspect(source_conn)
    if table_name not in inspector.get_table_names():
        logger.warning(f"Table '{table_name}' does not exist in source database; skipping.")
        return []

    result = source_conn.execute(text(f"SELECT * FROM {table_name} ORDER BY id ASC"))
    keys = list(result.keys())
    rows = [dict(zip(keys, row)) for row in result.fetchall()]
    return rows


def migrate_users(
    target_conn: Connection, rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    inserted, skipped = 0, 0
    if not rows:
        return 0, 0

    existing_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM users")).fetchall()}
    existing_emails = {r[0] for r in target_conn.execute(text("SELECT email FROM users")).fetchall()}

    insert_sql = text(
        """
        INSERT INTO users (
            id, name, email, hashed_password, age, weight, height,
            daily_calorie_limit, diet_type, goal_type, goal_target_days,
            goal_started_at, created_at
        ) VALUES (
            :id, :name, :email, :hashed_password, :age, :weight, :height,
            :daily_calorie_limit, :diet_type, :goal_type, :goal_target_days,
            :goal_started_at, :created_at
        )
        """
    )

    for row in rows:
        if row["id"] in existing_ids or row["email"] in existing_emails:
            skipped += 1
            continue

        target_conn.execute(
            insert_sql,
            {
                "id": row["id"],
                "name": row.get("name"),
                "email": row["email"],
                "hashed_password": row.get("hashed_password") or "",
                "age": row.get("age"),
                "weight": row.get("weight"),
                "height": row.get("height"),
                "daily_calorie_limit": row.get("daily_calorie_limit") or 2000,
                "diet_type": row.get("diet_type"),
                "goal_type": row.get("goal_type"),
                "goal_target_days": row.get("goal_target_days") or 30,
                "goal_started_at": row.get("goal_started_at"),
                "created_at": row["created_at"],
            },
        )
        existing_ids.add(row["id"])
        existing_emails.add(row["email"])
        inserted += 1

    return inserted, skipped


def migrate_products(
    target_conn: Connection, rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    inserted, skipped = 0, 0
    if not rows:
        return 0, 0

    existing_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM products")).fetchall()}
    existing_barcodes = {r[0] for r in target_conn.execute(text("SELECT barcode FROM products")).fetchall()}

    insert_sql = text(
        """
        INSERT INTO products (
            id, barcode, product_name, brand, nutriscore,
            ingredients, additives, created_at
        ) VALUES (
            :id, :barcode, :product_name, :brand, :nutriscore,
            :ingredients, :additives, :created_at
        )
        """
    )

    for row in rows:
        if row["id"] in existing_ids or row["barcode"] in existing_barcodes:
            skipped += 1
            continue

        target_conn.execute(
            insert_sql,
            {
                "id": row["id"],
                "barcode": row["barcode"],
                "product_name": row["product_name"],
                "brand": row.get("brand"),
                "nutriscore": row.get("nutriscore"),
                "ingredients": row.get("ingredients"),
                "additives": row.get("additives"),
                "created_at": row["created_at"],
            },
        )
        existing_ids.add(row["id"])
        existing_barcodes.add(row["barcode"])
        inserted += 1

    return inserted, skipped


def migrate_nutrition(
    target_conn: Connection, rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    inserted, skipped = 0, 0
    if not rows:
        return 0, 0

    existing_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM nutrition")).fetchall()}
    product_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM products")).fetchall()}

    insert_sql = text(
        """
        INSERT INTO nutrition (
            id, product_id, calories, fat, sugar, salt, protein, fiber, carbs
        ) VALUES (
            :id, :product_id, :calories, :fat, :sugar, :salt, :protein, :fiber, :carbs
        )
        """
    )

    for row in rows:
        if row["id"] in existing_ids:
            skipped += 1
            continue

        # Foreign key validation: product must exist
        if row["product_id"] not in product_ids:
            logger.warning(
                f"Skipping nutrition id={row['id']} because product_id={row['product_id']} not found in target."
            )
            skipped += 1
            continue

        target_conn.execute(
            insert_sql,
            {
                "id": row["id"],
                "product_id": row["product_id"],
                "calories": row.get("calories"),
                "fat": row.get("fat"),
                "sugar": row.get("sugar"),
                "salt": row.get("salt"),
                "protein": row.get("protein"),
                "fiber": row.get("fiber"),
                "carbs": row.get("carbs"),
            },
        )
        existing_ids.add(row["id"])
        inserted += 1

    return inserted, skipped


def migrate_scan_history(
    target_conn: Connection, rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    inserted, skipped = 0, 0
    if not rows:
        return 0, 0

    existing_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM scan_history")).fetchall()}
    user_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM users")).fetchall()}

    insert_sql = text(
        """
        INSERT INTO scan_history (
            id, user_id, barcode, scan_time, result
        ) VALUES (
            :id, :user_id, :barcode, :scan_time, :result
        )
        """
    )

    for row in rows:
        if row["id"] in existing_ids:
            skipped += 1
            continue

        # Foreign key validation: user must exist
        uid = row.get("user_id") or 1
        if uid not in user_ids:
            logger.warning(f"Scan history id={row['id']} references missing user_id={uid}; skipping.")
            skipped += 1
            continue

        target_conn.execute(
            insert_sql,
            {
                "id": row["id"],
                "user_id": uid,
                "barcode": row["barcode"],
                "scan_time": row["scan_time"],
                "result": row["result"],
            },
        )
        existing_ids.add(row["id"])
        inserted += 1

    return inserted, skipped


def migrate_daily_food_log(
    target_conn: Connection, rows: List[Dict[str, Any]]
) -> Tuple[int, int]:
    inserted, skipped = 0, 0
    if not rows:
        return 0, 0

    existing_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM daily_food_log")).fetchall()}
    user_ids = {r[0] for r in target_conn.execute(text("SELECT id FROM users")).fetchall()}

    insert_sql = text(
        """
        INSERT INTO daily_food_log (
            id, user_id, barcode, product_name, calories, fat, sugar,
            salt, protein, fiber, carbs, consumed_at
        ) VALUES (
            :id, :user_id, :barcode, :product_name, :calories, :fat, :sugar,
            :salt, :protein, :fiber, :carbs, :consumed_at
        )
        """
    )

    for row in rows:
        if row["id"] in existing_ids:
            skipped += 1
            continue

        # Foreign key validation: user must exist
        uid = row.get("user_id") or 1
        if uid not in user_ids:
            logger.warning(f"Daily food log id={row['id']} references missing user_id={uid}; skipping.")
            skipped += 1
            continue

        target_conn.execute(
            insert_sql,
            {
                "id": row["id"],
                "user_id": uid,
                "barcode": row["barcode"],
                "product_name": row["product_name"],
                "calories": row.get("calories"),
                "fat": row.get("fat"),
                "sugar": row.get("sugar"),
                "salt": row.get("salt"),
                "protein": row.get("protein"),
                "fiber": row.get("fiber"),
                "carbs": row.get("carbs"),
                "consumed_at": row["consumed_at"],
            },
        )
        existing_ids.add(row["id"])
        inserted += 1

    return inserted, skipped


def reset_postgres_sequences(conn: Connection) -> None:
    """Reset PostgreSQL serial sequences to MAX(id) for all tables."""
    if conn.dialect.name != "postgresql":
        return

    logger.info("Resetting PostgreSQL sequences...")
    for table in MIGRATION_TABLES:
        try:
            conn.execute(
                text(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        (SELECT MAX(id) IS NOT NULL FROM {table})
                    );
                    """
                )
            )
            logger.info(f"Sequence for '{table}' successfully reset.")
        except Exception as e:
            logger.warning(f"Could not reset sequence for table '{table}': {e}")


def execute_migration(
    sqlite_path: Path,
    pg_url: str,
    dry_run: bool = False,
) -> Dict[str, Dict[str, int]]:
    """Execute complete migration pipeline with transactional safety."""
    logger.info(f"Source SQLite database: {sqlite_path}")
    logger.info(f"Target Database URL: {redact_database_url(pg_url)}")

    source_engine = get_sqlite_engine(sqlite_path)
    target_engine = get_target_engine(pg_url)

    # 1. Read source data
    source_data: Dict[str, List[Dict[str, Any]]] = {}
    with source_engine.connect() as source_conn:
        for table in MIGRATION_TABLES:
            rows = extract_table_rows(source_conn, table)
            source_data[table] = rows
            logger.info(f"Read {len(rows)} rows from source table '{table}'")

    summary: Dict[str, Dict[str, int]] = {}

    # 2. Perform transactional insertion on destination
    with target_engine.connect() as target_conn:
        trans = target_conn.begin()
        try:
            # Users
            u_ins, u_skip = migrate_users(target_conn, source_data["users"])
            summary["users"] = {"source": len(source_data["users"]), "inserted": u_ins, "skipped": u_skip}

            # Products
            p_ins, p_skip = migrate_products(target_conn, source_data["products"])
            summary["products"] = {"source": len(source_data["products"]), "inserted": p_ins, "skipped": p_skip}

            # Nutrition
            n_ins, n_skip = migrate_nutrition(target_conn, source_data["nutrition"])
            summary["nutrition"] = {"source": len(source_data["nutrition"]), "inserted": n_ins, "skipped": n_skip}

            # Scan History
            s_ins, s_skip = migrate_scan_history(target_conn, source_data["scan_history"])
            summary["scan_history"] = {"source": len(source_data["scan_history"]), "inserted": s_ins, "skipped": s_skip}

            # Daily Food Log
            d_ins, d_skip = migrate_daily_food_log(target_conn, source_data["daily_food_log"])
            summary["daily_food_log"] = {"source": len(source_data["daily_food_log"]), "inserted": d_ins, "skipped": d_skip}

            # Sequences
            if not dry_run:
                reset_postgres_sequences(target_conn)
                trans.commit()
                logger.info("Transaction committed successfully.")
            else:
                trans.rollback()
                logger.info("Dry-run requested: transaction rolled back.")

        except Exception as e:
            trans.rollback()
            logger.error(f"Migration failed with error: {e}. Transaction rolled back.")
            raise

    # 3. Post-migration row counts verification
    with target_engine.connect() as target_conn:
        for table in MIGRATION_TABLES:
            try:
                cnt = target_conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
                summary[table]["destination_total"] = cnt
            except Exception:
                summary[table]["destination_total"] = -1

    return summary


def print_summary_report(summary: Dict[str, Dict[str, int]], dry_run: bool) -> None:
    """Print readable migration audit report."""
    print("\n" + "=" * 70)
    print("PRAMAAN DATABASE MIGRATION SUMMARY REPORT" + (" [DRY-RUN]" if dry_run else ""))
    print("=" * 70)
    print(f"{'Table':<20} {'Source':<10} {'Inserted':<12} {'Skipped':<10} {'Destination Total':<18}")
    print("-" * 70)
    for table, stats in summary.items():
        print(
            f"{table:<20} {stats['source']:<10} {stats['inserted']:<12} {stats['skipped']:<10} {stats.get('destination_total', 0):<18}"
        )
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=BASE_DIR / "database" / "foodscanner.db",
        help="Path to source SQLite database file.",
    )
    parser.add_argument(
        "--pg-url",
        type=str,
        default=os.getenv("DATABASE_URL", ""),
        help="Target PostgreSQL connection URL (defaults to DATABASE_URL env var).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without committing changes.",
    )

    args = parser.parse_args()

    if not args.pg_url:
        logger.error("No target DATABASE_URL specified via --pg-url or environment variable.")
        sys.exit(1)

    try:
        summary = execute_migration(
            sqlite_path=args.sqlite_path,
            pg_url=args.pg_url,
            dry_run=args.dry_run,
        )
        print_summary_report(summary, dry_run=args.dry_run)
    except Exception as e:
        logger.error(f"Migration aborted: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
