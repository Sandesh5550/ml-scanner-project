from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pandas as pd


DATASET_PATH = Path(__file__).resolve().parents[1] / "datasets" / "indian_foods" / "indian_packaged_foods.csv"


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _load_dataset(path: Path = DATASET_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Indian foods dataset not found: {path}")
    return pd.read_csv(path)


def search_indian_dataset(product_name: str) -> Optional[dict]:
    name = (product_name or "").strip()
    if not name:
        return None

    df = _load_dataset()
    mask = df["product_name"].astype(str).str.lower() == name.lower()
    if not mask.any():
        contains = df["product_name"].astype(str).str.lower().str.contains(name.lower(), na=False)
        if not contains.any():
            return None
        row = df[contains].iloc[0]
    else:
        row = df[mask].iloc[0]

    return {
        "product_name": str(row.get("product_name")),
        "calories": _to_float(row.get("calories")),
        "fat": _to_float(row.get("fat")),
        "saturated_fat": _to_float(row.get("saturated_fat")),
        "trans_fat": _to_float(row.get("trans_fat")),
        "sugar": _to_float(row.get("sugar")),
        "salt": _to_float(row.get("salt")),
        "protein": _to_float(row.get("protein")),
        "fiber": _to_float(row.get("fiber")),
        "carbs": _to_float(row.get("carbs")),
        "ingredients": None,
        "additives": None,
        "source": "indian_dataset",
    }
