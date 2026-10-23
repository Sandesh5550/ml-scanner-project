from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import httpx
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from services import db_service


INDIAN_DATASET_PATH = Path(__file__).resolve().parents[1] / "datasets" / "indian_foods" / "indian_packaged_foods.csv"
OPENFOODFACTS_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"


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


def _dedupe_and_limit(items: list[dict], limit: int = 10) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        name = _normalize_name(item.get("product_name"))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"product_name": name, "source": item.get("source")})
        if len(out) >= limit:
            break
    return out


def _rank_and_dedupe(items: list[dict], limit: int = 10) -> list[dict]:
    best: dict[str, dict] = {}
    for item in items:
        name = _normalize_name(item.get("product_name"))
        if not name:
            continue
        key = name.lower()
        score = float(item.get("_score") or 0.0)

        existing = best.get(key)
        if existing is None or float(existing.get("_score") or 0.0) < score:
            best[key] = {"product_name": name, "source": item.get("source"), "_score": score}

    ranked = sorted(best.values(), key=lambda x: float(x.get("_score") or 0.0), reverse=True)
    return [{"product_name": r["product_name"], "source": r.get("source"), "similarity_score": round(r["_score"], 1)} for r in ranked[:limit]]


def _search_local_db(db: Session, query: str, limit: int = 10) -> list[dict]:
    q = query.strip()
    if not q:
        return []

    candidates = db_service.get_product_name_candidates(db, limit=200)

    scored: list[tuple[float, dict]] = []
    for name in candidates:
        product_name = _normalize_name(name)
        score = _similarity(q, product_name)
        if score >= 65:
            scored.append((score, {"product_name": product_name, "source": "local", "_score": score}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _search_indian_dataset(query: str, limit: int = 10) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    if not INDIAN_DATASET_PATH.exists():
        return []

    try:
        df = pd.read_csv(INDIAN_DATASET_PATH)
    except Exception:
        return []

    if "product_name" not in df.columns:
        return []

    scored: list[tuple[float, dict]] = []
    for _, row in df.iterrows():
        name = _normalize_name(row.get("product_name"))
        score = _similarity(q, name)
        if score >= 65:
            scored.append((score, {"product_name": name, "source": "indian_dataset", "_score": score}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _search_openfoodfacts(query: str, limit: int = 10) -> list[dict]:
    q = query.strip()
    if not q:
        return []

    params = {
        "search_terms": q,
        "search_simple": 1,
        "action": "process",
        "json": 1,
    }

    try:
        resp = httpx.get(
            OPENFOODFACTS_SEARCH_URL,
            params=params,
            timeout=httpx.Timeout(5.0, connect=3.0),
            headers={"User-Agent": "Pramaan-FoodScanner/1.0 (+https://github.com/pramaan)"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    products = data.get("products") or []
    scored: list[tuple[float, dict]] = []
    for p in products:
        name = _normalize_name(p.get("product_name"))
        if not name:
            continue
        score = _similarity(q, name)
        if score >= 65:
            scored.append((score, {"product_name": name, "source": "openfoodfacts", "_score": score}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def search_products(db: Session, query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    combined: list[dict] = []
    combined.extend(_search_local_db(db, q, limit=10))
    combined.extend(_search_indian_dataset(q, limit=10))
    combined.extend(_search_openfoodfacts(q, limit=30))

    # Global rank by similarity, then dedupe keeping best score per product name.
    return _rank_and_dedupe(combined, limit=10)
