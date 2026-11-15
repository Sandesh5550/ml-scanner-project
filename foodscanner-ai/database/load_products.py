from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.database import get_connection


def load_products(
    cleaned_csv: Path,
    db_path: Path,
    chunksize: int,
    batch_size: int,
) -> None:
    if not cleaned_csv.exists():
        raise FileNotFoundError(
            f"Cleaned dataset not found: {cleaned_csv}. Run datasets/prepare_dataset.py first."
        )

    conn = get_connection(db_path)
    try:
        total_rows = 0
        inserted_products = 0

        product_rows: list[tuple[str, str, str | None]] = []
        nutrition_rows: list[tuple[int, float | None, float | None, float | None, float | None, float | None, float | None, float | None]] = []

        def flush() -> None:
            nonlocal inserted_products
            if not product_rows:
                return

            conn.executemany(
                """
                INSERT INTO products (barcode, product_name, nutriscore)
                VALUES (?, ?, ?)
                ON CONFLICT(barcode) DO UPDATE SET
                    product_name=excluded.product_name,
                    nutriscore=COALESCE(excluded.nutriscore, products.nutriscore)
                """,
                product_rows,
            )

            # Parameterized placeholder generation; no user input interpolated into query structure
            rows = conn.execute(
                "SELECT id, barcode FROM products WHERE barcode IN ({})".format(  # nosec B608
                    ",".join(["?"] * len(product_rows))
                ),
                [r[0] for r in product_rows],
            ).fetchall()
            barcode_to_id = {r["barcode"]: int(r["id"]) for r in rows}

            resolved_nutrition: list[
                tuple[int, float | None, float | None, float | None, float | None, float | None, float | None, float | None]
            ] = []
            for (barcode, calories, fat, sugar, salt, protein, fiber, carbs) in nutrition_rows:
                pid = barcode_to_id.get(barcode)
                if pid is None:
                    continue
                resolved_nutrition.append((pid, calories, fat, sugar, salt, protein, fiber, carbs))

            conn.executemany(
                """
                INSERT INTO nutrition (product_id, calories, fat, sugar, salt, protein, fiber, carbs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                resolved_nutrition,
            )

            conn.commit()
            inserted_products += len(product_rows)
            product_rows.clear()
            nutrition_rows.clear()

        reader = pd.read_csv(
            cleaned_csv,
            chunksize=chunksize,
            dtype={"barcode": "string", "product_name": "string", "nutriscore_grade": "string"},
            low_memory=False,
            encoding_errors="replace",
        )

        for chunk in reader:
            total_rows += len(chunk)

            chunk["barcode"] = chunk["barcode"].astype("string").str.strip()
            chunk["product_name"] = chunk["product_name"].astype("string").str.strip()

            chunk = chunk.dropna(subset=["barcode", "product_name"])

            if "nutriscore_grade" in chunk.columns:
                chunk["nutriscore_grade"] = (
                    chunk["nutriscore_grade"]
                    .astype("string")
                    .str.strip()
                    .str.lower()
                    .replace({"": pd.NA})
                )

            numeric_cols = ["calories", "fat", "sugar", "salt", "protein", "fiber", "carbs"]
            for col in numeric_cols:
                if col in chunk.columns:
                    chunk[col] = pd.to_numeric(chunk[col], errors="coerce")

            for row in chunk.itertuples(index=False):
                barcode = getattr(row, "barcode")
                product_name = getattr(row, "product_name")
                nutriscore = getattr(row, "nutriscore_grade", None)

                product_rows.append((str(barcode), str(product_name), None if pd.isna(nutriscore) else str(nutriscore)))

                nutrition_rows.append(
                    (
                        str(barcode),
                        getattr(row, "calories", None),
                        getattr(row, "fat", None),
                        getattr(row, "sugar", None),
                        getattr(row, "salt", None),
                        getattr(row, "protein", None),
                        getattr(row, "fiber", None),
                        getattr(row, "carbs", None),
                    )
                )

                if len(product_rows) >= batch_size:
                    flush()

        flush()

        count_products = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
        count_nutrition = conn.execute("SELECT COUNT(*) AS c FROM nutrition").fetchone()["c"]

        print("Load complete")
        print(f"Rows read from cleaned CSV: {total_rows}")
        print(f"Products upserted: {inserted_products}")
        print(f"Products in DB: {count_products}")
        print(f"Nutrition rows in DB: {count_nutrition}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load cleaned Open Food Facts data into SQLite")
    parser.add_argument(
        "--cleaned-csv",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "datasets" / "clean_food_data.csv",
        help="Path to cleaned dataset",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path(__file__).resolve().parent / "foodscanner.db",
        help="Path to SQLite DB",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=200_000,
        help="Rows per pandas chunk",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5_000,
        help="Rows per SQLite batch",
    )

    args = parser.parse_args()
    load_products(args.cleaned_csv, args.db_path, args.chunksize, args.batch_size)


if __name__ == "__main__":
    main()
