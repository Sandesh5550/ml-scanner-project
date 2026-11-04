from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional


DB_PATH = Path(__file__).resolve().parent / "foodscanner.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def insert_product(
    conn: sqlite3.Connection,
    *,
    barcode: str,
    product_name: str,
    nutriscore: Optional[str] = None,
    ingredients: Optional[str] = None,
    additives: Optional[str] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO products (barcode, product_name, nutriscore, ingredients, additives)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(barcode) DO UPDATE SET
            product_name=excluded.product_name,
            nutriscore=COALESCE(excluded.nutriscore, products.nutriscore),
            ingredients=COALESCE(excluded.ingredients, products.ingredients),
            additives=COALESCE(excluded.additives, products.additives)
        """,
        (barcode, product_name, nutriscore, ingredients, additives),
    )

    row = conn.execute("SELECT id FROM products WHERE barcode = ?", (barcode,)).fetchone()
    if row is None:
        if cur.lastrowid is None:
            raise RuntimeError("Failed to fetch product id after insert")
        return int(cur.lastrowid)

    try:
        return int(row["id"])
    except (TypeError, KeyError, IndexError):
        return int(row[0])


def insert_nutrition(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    calories: Optional[float] = None,
    fat: Optional[float] = None,
    sugar: Optional[float] = None,
    salt: Optional[float] = None,
    protein: Optional[float] = None,
    fiber: Optional[float] = None,
    carbs: Optional[float] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nutrition (product_id, calories, fat, sugar, salt, protein, fiber, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (product_id, calories, fat, sugar, salt, protein, fiber, carbs),
    )
    return int(cur.lastrowid)


def get_product_by_barcode(conn: sqlite3.Connection, barcode: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT
            p.id,
            p.barcode,
            p.product_name,
            p.nutriscore,
            p.ingredients,
            p.additives,
            p.created_at,
            n.calories,
            n.fat,
            n.sugar,
            n.salt,
            n.protein,
            n.fiber,
            n.carbs
        FROM products p
        LEFT JOIN nutrition n ON n.product_id = p.id
        WHERE p.barcode = ?
        """,
        (barcode,),
    ).fetchone()

    if row is None:
        return None

    return dict(row)


def upsert_product_and_nutrition(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    product_id = insert_product(
        conn,
        barcode=str(data["barcode"]),
        product_name=str(data["product_name"]),
        nutriscore=data.get("nutriscore"),
        ingredients=data.get("ingredients"),
        additives=data.get("additives"),
    )

    insert_nutrition(
        conn,
        product_id=product_id,
        calories=data.get("calories"),
        fat=data.get("fat"),
        sugar=data.get("sugar"),
        salt=data.get("salt"),
        protein=data.get("protein"),
        fiber=data.get("fiber"),
        carbs=data.get("carbs"),
    )
    conn.commit()

    stored = get_product_by_barcode(conn, str(data["barcode"]))
    if stored is None:
        raise RuntimeError("Failed to fetch product after upsert")
    return stored


def get_recent_scans(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, barcode, result, scan_time
        FROM scan_history
        ORDER BY scan_time DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()

    return [{"id": r[0], "barcode": r[1], "result": r[2], "scan_time": r[3]} for r in rows]
