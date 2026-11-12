from __future__ import annotations

import hashlib
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
_INDIAN_DATASET_DF: Optional[pd.DataFrame] = None
_DF_LOCK = threading.Lock()

KNOWN_ACRONYMS = {
    "KFC", "MDH", "ITC", "IBM", "BLT", "DKNY", "GMC", "USA", "UK", "FSSAI",
    "B&G", "M&S", "LG", "BMW", "BBQ"
}


def normalize_brand(brand_raw: Any) -> Optional[str]:
    """Normalize brand strings cleanly while preserving intentional capitalization.

    Rules:
    - Multiple comma-separated brands (common in OpenFoodFacts) take the primary brand.
    - Intentional mixed-case brands (e.g. 'iPhone', 'McVitie's', 'Lay's', 'Parle-G', 'Bio Choice')
      are strictly preserved and NOT blindly title-cased.
    - Short uppercase acronyms (<= 3 letters or in KNOWN_ACRONYMS, e.g. 'KFC', 'MDH', 'M&S')
      remain uppercase.
    - Pure uppercase words (e.g. 'AMUL', 'BRITANNIA', 'CADBURY', 'NESTLE') are normalized
      to title/capital case ('Amul', 'Britannia', 'Cadbury', 'Nestle'), correctly preserving
      lowercase possessives (e.g. "KELLOGG'S" -> "Kellogg's").
    - Pure lowercase words (e.g. 'liebig', 'wolf brand') are capitalized ('Liebig', 'Wolf Brand').
    """
    if brand_raw is None:
        return None
    b = str(brand_raw).strip()
    if not b:
        return None

    # Take first brand if comma-separated
    if "," in b:
        b = b.split(",")[0].strip()
        if not b:
            return None

    # Check for mixed case: both upper and lower letters present
    has_upper = any(c.isupper() for c in b)
    has_lower = any(c.islower() for c in b)

    # 1. Preserve meaningful mixed-case (e.g. iPhone, McVitie's, Lay's, Parle-G, Bio Choice, Coca-Cola)
    if has_upper and has_lower:
        return b

    # 2. Preserve short uppercase acronyms (e.g. KFC, MDH, M&S, ITC, LG, BMW)
    alpha_letters = "".join(c for c in b if c.isalpha())
    if b.isupper() and (len(alpha_letters) <= 3 or b in KNOWN_ACRONYMS):
        return b

    # 3. Format all-caps (e.g. AMUL, BRITANNIA) or all-lowercase (e.g. liebig, wolf brand)
    words = b.split()
    formatted = []
    for w in words:
        w_alpha = "".join(c for c in w if c.isalpha())
        if w.isupper() and (len(w_alpha) <= 3 or w in KNOWN_ACRONYMS):
            formatted.append(w)
        elif "'" in w:
            parts = w.split("'", 1)
            formatted.append(parts[0].capitalize() + "'" + parts[1].lower())
        elif "-" in w:
            parts = w.split("-")
            formatted.append("-".join(p.capitalize() for p in parts))
        else:
            formatted.append(w.capitalize())

    return " ".join(formatted)


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


def get_indian_dataset_df() -> Optional[pd.DataFrame]:
    global _INDIAN_DATASET_DF
    if _INDIAN_DATASET_DF is not None:
        return _INDIAN_DATASET_DF

    with _DF_LOCK:
        if _INDIAN_DATASET_DF is not None:
            return _INDIAN_DATASET_DF

        if not INDIAN_DATASET_PATH.exists():
            return None

        try:
            df = pd.read_csv(
                INDIAN_DATASET_PATH,
                dtype={"product_name": "string", "brand": "string"},
                low_memory=False,
                encoding_errors="replace",
            )
            _INDIAN_DATASET_DF = df
            return df
        except Exception as exc:
            logger.warning("Failed to load local Indian packaged foods dataset: %s", exc)
            return None


def _search_local_db(db: Session, query: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []

    stmt = text("""
        SELECT p.id, p.barcode, p.product_name, p.brand, p.nutriscore, p.ingredients,
               n.calories, n.fat, n.saturated_fat, n.trans_fat, n.sugar, n.salt, n.protein, n.fiber, n.carbs
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
        brand = normalize_brand(r.brand)
        ingr = _normalize_name(r.ingredients)
        if not name:
            continue

        # If brand is missing in local DB, check curated Indian dataset for exact product name match
        if not brand and name:
            ind_df = get_indian_dataset_df()
            if ind_df is not None and "product_name" in ind_df.columns and "brand" in ind_df.columns:
                match = ind_df[ind_df["product_name"].astype(str).str.strip().str.lower() == name.lower()]
                if not match.empty:
                    cand_brand = normalize_brand(match.iloc[0].get("brand"))
                    if cand_brand:
                        brand = cand_brand

        name_lower = name.lower()
        brand_lower = (brand or "").lower()

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
                "saturated_fat": _to_optional_float(getattr(r, "saturated_fat", None)),
                "trans_fat": _to_optional_float(getattr(r, "trans_fat", None)),
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


def _search_indian_dataset(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return []

    df = get_indian_dataset_df()
    if df is None:
        return []

    pat = re.escape(q)
    try:
        cond = (
            df["product_name"].str.contains(pat, case=False, na=False)
            | df["brand"].str.contains(pat, case=False, na=False)
        )
        subset = df[cond].head(limit * 2)
    except Exception as e:
        logger.warning("Indian dataset regex error: %s", e)
        return []

    q_lower = q.lower()
    results: List[Dict[str, Any]] = []

    for _, r in subset.iterrows():
        name = _normalize_name(r.get("product_name"))
        brand = normalize_brand(r.get("brand"))
        if not name:
            continue

        name_lower = name.lower()
        brand_lower = (brand or "").lower()

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
        else:
            sim = _similarity(q, name)
            if sim < 60.0:
                continue
            score = sim

        name_hash = hashlib.md5(name.lower().encode("utf-8")).hexdigest()
        synthetic_barcode = f"IND-{name_hash}"

        results.append({
            "barcode": synthetic_barcode,
            "product_name": name,
            "brand": brand,
            "nutriscore": None,
            "ingredients": None,
            "nutrition": {
                "calories": _to_optional_float(r.get("calories")),
                "fat": _to_optional_float(r.get("fat")),
                "sugar": _to_optional_float(r.get("sugar")),
                "salt": _to_optional_float(r.get("salt")),
                "protein": _to_optional_float(r.get("protein")),
                "fiber": _to_optional_float(r.get("fiber")),
                "carbs": _to_optional_float(r.get("carbs")),
            },
            "source": "indian_dataset",
            "_score": score,
        })

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:limit]


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
        brand = normalize_brand(r.get("brands"))
        ingr = _normalize_name(r.get("ingredients_text"))
        if not name:
            continue

        name_lower = name.lower()
        brand_lower = (brand or "").lower()

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

    try:
        from services.off_client import get_default_off_client, OFFStatus

        off_client = get_default_off_client()
        # Enforce strict 1.5-second remote search timeout and dedicated "search" rate budget (4 req/min)
        search_res = off_client.search(query=q, limit=limit, timeout=1.5)

        if search_res.status != OFFStatus.SUCCESS or not search_res.products:
            return []

        scored: List[Dict[str, Any]] = []
        for p in search_res.products:
            name = _normalize_name(p.get("product_name"))
            if not name:
                continue
            score = _similarity(q, name)
            if score >= 65:
                nutr = p.get("nutrition") or {}
                scored.append({
                    "barcode": str(p.get("barcode") or ""),
                    "product_name": name,
                    "brand": normalize_brand(p.get("brand") or p.get("brands")),
                    "nutriscore": str(p.get("nutriscore") or "").lower() or None,
                    "ingredients": _normalize_name(p.get("ingredients")) or None,
                    "nutrition": {
                        "calories": _to_optional_float(nutr.get("calories")),
                        "fat": _to_optional_float(nutr.get("fat")),
                        "sugar": _to_optional_float(nutr.get("sugar")),
                        "salt": _to_optional_float(nutr.get("salt")),
                        "protein": _to_optional_float(nutr.get("protein")),
                        "fiber": _to_optional_float(nutr.get("fiber")),
                        "carbs": _to_optional_float(nutr.get("carbs")),
                    },
                    "source": "openfoodfacts_api",
                    "_score": score,
                })

        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored[:limit]
    except Exception as exc:
        logger.warning(f"Remote OpenFoodFacts search failed gracefully: {exc}")
        return []


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
            if existing is not None and not item_copy.get("brand") and existing.get("brand"):
                item_copy["brand"] = existing.get("brand")
            best[key] = item_copy
        elif existing is not None:
            if not existing.get("brand") and item.get("brand"):
                existing["brand"] = item.get("brand")

    ranked = sorted(
        best.values(),
        key=lambda x: (
            float(x.get("_score") or 0.0),
            1 if x.get("brand") else 0,
        ),
        reverse=True,
    )

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
    # 1. Database matches (local cache / persisted products)
    local_matches = _search_local_db(db, q, limit=limit)
    combined.extend(local_matches)

    # 2. Canonical Indian packaged foods dataset (313 curated Indian products)
    indian_matches = _search_indian_dataset(q, limit=limit)
    combined.extend(indian_matches)

    # 3. Local OpenFoodFacts dataset (37,444 cleaned rows)
    dataset_matches = _search_openfoodfacts_dataset(q, limit=limit)
    combined.extend(dataset_matches)

    # 4. If very few results and query is 3+ chars, try remote OpenFoodFacts as fallback
    if len(combined) < 3 and len(q) >= 3:
        remote_matches = _search_openfoodfacts_remote(q, limit=10)
        combined.extend(remote_matches)

    return _rank_and_dedupe(combined, limit=limit)
