from __future__ import annotations

import logging
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"
INDIAN_DATASET_PATH = DATASETS_DIR / "indian_foods" / "indian_packaged_foods.csv"
OPENFOODFACTS_CLEANED_PATH = DATASETS_DIR / "large_openfoodfacts_cleaned.csv"
OPENFOODFACTS_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"

_OPENFOODFACTS_DF: Optional[pd.DataFrame] = None
_DF_LOCK = threading.Lock()


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return None if f != f else round(f, 2)
    except (TypeError, ValueError):
        return None


def _normalize_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _similarity(query: str, name: str) -> float:
    q = (query or "").strip().lower()
    n = (name or "").strip().lower()
    if not q or not n:
        return 0.0

    token_set = float(fuzz.token_set_ratio(q, n))
    token_sort = float(fuzz.token_sort_ratio(q, n))
    return (token_set + token_sort) / 2.0


def get_openfoodfacts_df() -> Optional[pd.DataFrame]:
    global _OPENFOODFACTS_DF
    if _OPENFOODFACTS_DF is not None:
        return _OPENFOODFACTS_DF

    with _DF_LOCK:
        if _OPENFOODFACTS_DF is not None:
            return _OPENFOODFACTS_DF

        csv_path = OPENFOODFACTS_CLEANED_PATH
        if not csv_path.exists():
            csv_path = DATASETS_DIR / "large_openfoodfacts.csv"
        if not csv_path.exists():
            return None

        try:
            df = pd.read_csv(
                csv_path,
                dtype={"code": "string", "product_name": "string", "brands": "string", "ingredients_text": "string"},
                low_memory=False,
                encoding_errors="replace",
            )
            _OPENFOODFACTS_DF = df
            return df
        except Exception as exc:
            logger.warning("Failed to load local OpenFoodFacts dataset: %s", exc)
            return None


def _search_local_db(db: Session, query: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []

    stmt = text("""
        SELECT p.id, p.barcode, p.product_name, p.brand, p.nutriscore, p.ingredients,
               n.calories, n.fat, n.sugar, n.salt, n.protein, n.fiber, n.carbs
        FROM products p
        LEFT JOIN nutrition n ON n.product_id = p.id
        WHERE p.product_name LIKE :term ESCAPE '/'
           OR p.brand LIKE :term ESCAPE '/'
           OR p.ingredients LIKE :term ESCAPE '/'
        LIMIT :sql_limit
    """)

    # Escape LIKE wildcards so user input is treated as literal search string
    escaped_q = q.replace("/", "//").replace("%", "/%").replace("_", "/_")
    term = f"%{escaped_q}%"
    try:
        rows = db.execute(stmt, {"term": term, "sql_limit": max(limit * 3, 50)}).fetchall()
    except Exception as e:
        logger.error("Database search error: %s", e)
        return []

    q_lower = q.lower()
    scored: List[Dict[str, Any]] = []

    for r in rows:
        name = _normalize_name(r.product_name)
        brand = _normalize_name(r.brand)
        ingr = _normalize_name(r.ingredients)
        if not name:
            continue

        name_lower = name.lower()
        brand_lower = brand.lower()

        # Relevance scoring
        if q_lower == name_lower:
            score = 100.0
        elif name_lower.startswith(q_lower):
            score = 95.0
        elif brand_lower and (brand_lower == q_lower or brand_lower.startswith(q_lower)):
            score = 90.0
        elif q_lower in name_lower:
            score = 85.0
        elif brand_lower and q_lower in brand_lower:
            score = 80.0
        elif ingr and q_lower in ingr.lower():
            score = 75.0
        else:
            sim = _similarity(q, name)
            if sim < 60.0:
                continue
            score = sim

        barcode = str(r.barcode or "")
        src = "indian_dataset" if barcode.startswith("IND-") else "local_db"

        scored.append({
            "barcode": barcode,
            "product_name": name,
            "brand": brand or None,
            "nutriscore": str(r.nutriscore).lower() if r.nutriscore else None,
            "ingredients": ingr or None,
            "nutrition": {
                "calories": _to_optional_float(r.calories),
                "fat": _to_optional_float(r.fat),
                "sugar": _to_optional_float(r.sugar),
                "salt": _to_optional_float(r.salt),
                "protein": _to_optional_float(r.protein),
                "fiber": _to_optional_float(r.fiber),
                "carbs": _to_optional_float(r.carbs),
            },
            "source": src,
            "_score": score,
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:limit]


def _search_openfoodfacts_dataset(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []

    df = get_openfoodfacts_df()
    if df is None:
        return []

    pat = re.escape(q)
    try:
        cond = (
            df["product_name"].str.contains(pat, case=False, na=False)
            | df["brands"].str.contains(pat, case=False, na=False)
            | df["ingredients_text"].str.contains(pat, case=False, na=False)
        )
        subset = df[cond].head(limit * 2)
    except Exception as e:
        logger.warning("Dataset search regex error: %s", e)
        return []

    q_lower = q.lower()
    results: List[Dict[str, Any]] = []

    for _, r in subset.iterrows():
        name = _normalize_name(r.get("product_name"))
        brand = _normalize_name(r.get("brands"))
        ingr = _normalize_name(r.get("ingredients_text"))
        if not name:
            continue

        name_lower = name.lower()
        brand_lower = brand.lower()

        if q_lower == name_lower:
            score = 98.0
        elif name_lower.startswith(q_lower):
            score = 92.0
        elif brand_lower and (brand_lower == q_lower or brand_lower.startswith(q_lower)):
            score = 88.0
        elif q_lower in name_lower:
            score = 82.0
        elif brand_lower and q_lower in brand_lower:
            score = 78.0
        else:
            score = 70.0

        results.append({
            "barcode": str(r.get("code") or ""),
            "product_name": name,
            "brand": brand or None,
            "nutriscore": str(r.get("nutriscore_grade") or "").lower() or None,
            "ingredients": ingr or None,
            "nutrition": {
                "calories": _to_optional_float(r.get("calories")),
                "fat": _to_optional_float(r.get("fat")),
                "sugar": _to_optional_float(r.get("sugar")),
                "salt": _to_optional_float(r.get("salt")),
                "protein": _to_optional_float(r.get("protein")),
                "fiber": _to_optional_float(r.get("fiber")),
                "carbs": _to_optional_float(r.get("carbs")),
            },
            "source": "openfoodfacts_dataset",
            "_score": score,
        })

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:limit]


def _search_openfoodfacts_remote(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []

    params = {
        "search_terms": q,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": limit,
    }

    try:
        resp = httpx.get(
            OPENFOODFACTS_SEARCH_URL,
            params=params,
            timeout=httpx.Timeout(2.5, connect=1.5),
            headers={"User-Agent": "Pramaan-FoodScanner/1.0 (+https://github.com/pramaan)"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    products = data.get("products") or []
    scored: List[Dict[str, Any]] = []
    for p in products:
        name = _normalize_name(p.get("product_name"))
        if not name:
            continue
        score = _similarity(q, name)
        if score >= 65:
            nutriments = p.get("nutriments") or {}
            scored.append({
                "barcode": str(p.get("code") or ""),
                "product_name": name,
                "brand": _normalize_name(p.get("brands")) or None,
                "nutriscore": str(p.get("nutriscore_grade") or "").lower() or None,
                "ingredients": _normalize_name(p.get("ingredients_text")) or None,
                "nutrition": {
                    "calories": _to_optional_float(nutriments.get("energy-kcal_100g") or nutriments.get("energy_100g")),
                    "fat": _to_optional_float(nutriments.get("fat_100g")),
                    "sugar": _to_optional_float(nutriments.get("sugars_100g")),
                    "salt": _to_optional_float(nutriments.get("salt_100g")),
                    "protein": _to_optional_float(nutriments.get("proteins_100g")),
                    "fiber": _to_optional_float(nutriments.get("fiber_100g")),
                    "carbs": _to_optional_float(nutriments.get("carbohydrates_100g")),
                },
                "source": "openfoodfacts_api",
                "_score": score,
            })

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:limit]


def _rank_and_dedupe(items: List[Dict[str, Any]], limit: int = 25) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}

    for item in items:
        name = _normalize_name(item.get("product_name"))
        if not name:
            continue

        barcode = str(item.get("barcode") or "").strip()
        brand = str(item.get("brand") or "").strip().lower()

        # Deduplication key: standard barcode or brand:product_name
        if barcode and barcode.isdigit() and len(barcode) >= 8:
            key = f"barcode:{barcode}"
        elif brand:
            key = f"brand_name:{brand}:{name.lower()}"
        else:
            key = f"name:{name.lower()}"

        score = float(item.get("_score") or 0.0)
        # Prioritize local database and Indian dataset over raw OpenFoodFacts
        source = str(item.get("source") or "")
        if source in ("local_db", "indian_dataset"):
            score += 5.0

        existing = best.get(key)
        if existing is None or float(existing.get("_score") or 0.0) < score:
            item_copy = dict(item)
            item_copy["_score"] = score
            best[key] = item_copy

    ranked = sorted(best.values(), key=lambda x: float(x.get("_score") or 0.0), reverse=True)

    output: List[Dict[str, Any]] = []
    for r in ranked[:limit]:
        output.append({
            "barcode": r.get("barcode") or "",
            "product_name": r["product_name"],
            "brand": r.get("brand"),
            "nutriscore": r.get("nutriscore"),
            "ingredients": r.get("ingredients"),
            "nutrition": r.get("nutrition") or {},
            "source": r.get("source"),
            "similarity_score": round(float(r.get("_score") or 0.0), 1),
        })

    return output


def search_products(db: Session, query: str, limit: int = 25) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    combined: List[Dict[str, Any]] = []
    # 1. SQLite database (contains local cache + 313 Indian packaged foods)
    local_matches = _search_local_db(db, q, limit=limit)
    combined.extend(local_matches)

    # 2. Local OpenFoodFacts dataset (42,902 cleaned rows)
    dataset_matches = _search_openfoodfacts_dataset(q, limit=limit)
    combined.extend(dataset_matches)

    # 3. If very few results and query is 3+ chars, try remote OpenFoodFacts as fallback
    if len(combined) < 3 and len(q) >= 3:
        remote_matches = _search_openfoodfacts_remote(q, limit=10)
        combined.extend(remote_matches)

    return _rank_and_dedupe(combined, limit=limit)
