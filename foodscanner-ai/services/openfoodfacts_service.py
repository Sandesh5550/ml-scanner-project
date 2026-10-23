from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx


logger = logging.getLogger(__name__)

API_URL_TEMPLATE = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
API_V2_URL_TEMPLATE = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
USER_AGENT = "Pramaan-FoodScanner/1.0 (+https://github.com/pramaan)"
DEFAULT_TIMEOUT = 8.0


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_product_dict(barcode: str, product: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(product, dict):
        return None

    nutriments = product.get("nutriments")
    if not isinstance(nutriments, dict):
        nutriments = {}

    additives_tags = product.get("additives_tags")
    additives: Optional[str]
    if isinstance(additives_tags, list):
        additives = ",".join(str(x).strip() for x in additives_tags if str(x).strip()) or None
    else:
        additives = None

    # Brand extraction and cleaning
    raw_brands = product.get("brands") or product.get("brand") or None
    brand: Optional[str] = None
    if raw_brands is not None:
        first_brand = str(raw_brands).split(",")[0].strip()
        brand = first_brand or None

    # Calories: prefer kcal, fall back to kJ converted to kcal
    calories = _to_float(nutriments.get("energy-kcal_100g") or nutriments.get("energy-kcal"))
    if calories is None:
        energy_kj = _to_float(nutriments.get("energy_100g") or nutriments.get("energy-kj_100g"))
        if energy_kj is not None and energy_kj > 0:
            calories = round(energy_kj / 4.184, 1)

    # Salt & sodium: sodium * 2.5 = salt
    salt = _to_float(nutriments.get("salt_100g") or nutriments.get("salt"))
    if salt is None:
        sodium = _to_float(nutriments.get("sodium_100g") or nutriments.get("sodium"))
        if sodium is not None:
            salt = round(sodium * 2.5, 3)

    grade = product.get("nutriscore_grade") or product.get("nutrition_grades")
    nutriscore_val = str(grade).strip().lower() if grade else None
    if nutriscore_val not in {"a", "b", "c", "d", "e"}:
        nutriscore_val = None

    normalized: dict[str, Any] = {
        "barcode": str(barcode).strip(),
        "product_name": product.get("product_name") or product.get("product_name_en"),
        "brand": brand,
        "nutriscore": nutriscore_val,
        "ingredients": product.get("ingredients_text") or product.get("ingredients_text_en"),
        "additives": additives,
        "calories": calories,
        "fat": _to_float(nutriments.get("fat_100g") or nutriments.get("fat")),
        "saturated_fat": _to_float(
            nutriments.get("saturated-fat_100g")
            or nutriments.get("saturated_fat_100g")
            or nutriments.get("saturated_fat")
        ),
        "sugar": _to_float(nutriments.get("sugars_100g") or nutriments.get("sugars") or nutriments.get("sugar")),
        "salt": salt,
        "protein": _to_float(nutriments.get("proteins_100g") or nutriments.get("proteins") or nutriments.get("protein")),
        "fiber": _to_float(nutriments.get("fiber_100g") or nutriments.get("fiber")),
        "carbs": _to_float(
            nutriments.get("carbohydrates_100g")
            or nutriments.get("carbohydrates")
            or nutriments.get("carbs")
        ),
    }

    if not normalized["product_name"]:
        return None

    for k in ("product_name", "brand", "nutriscore", "ingredients", "additives"):
        if normalized.get(k) is not None:
            normalized[k] = str(normalized[k]).strip() or None

    return normalized


def fetch_product_by_barcode(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v0 API synchronously using httpx."""
    url = API_URL_TEMPLATE.format(barcode=barcode)
    client_timeout = httpx.Timeout(timeout=timeout, connect=min(4.0, timeout))
    try:
        with httpx.Client(timeout=client_timeout, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError, Exception) as exc:
        logger.warning(f"OpenFoodFacts v0 fetch failed for {barcode}: {exc}")
        return None

    if not isinstance(data, dict) or data.get("status") != 1:
        return None

    return _normalize_product_dict(barcode, data.get("product") or {})


async def fetch_product_by_barcode_async(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v0 API asynchronously using httpx."""
    url = API_URL_TEMPLATE.format(barcode=barcode)
    client_timeout = httpx.Timeout(timeout=timeout, connect=min(4.0, timeout))
    try:
        async with httpx.AsyncClient(timeout=client_timeout, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError, Exception) as exc:
        logger.warning(f"OpenFoodFacts v0 async fetch failed for {barcode}: {exc}")
        return None

    if not isinstance(data, dict) or data.get("status") != 1:
        return None

    return _normalize_product_dict(barcode, data.get("product") or {})


def fetch_product_by_barcode_v2(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v2 API synchronously using httpx."""
    url = API_V2_URL_TEMPLATE.format(barcode=barcode)
    client_timeout = httpx.Timeout(timeout=timeout, connect=min(4.0, timeout))
    try:
        with httpx.Client(timeout=client_timeout, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError, Exception) as exc:
        logger.warning(f"OpenFoodFacts v2 fetch failed for {barcode}: {exc}")
        return None

    if not isinstance(data, dict):
        return None

    status = data.get("status")
    if status not in (1, "1", "success") and "product" not in data:
        return None

    return _normalize_product_dict(barcode, data.get("product") or {})


async def fetch_product_by_barcode_v2_async(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v2 API asynchronously using httpx."""
    url = API_V2_URL_TEMPLATE.format(barcode=barcode)
    client_timeout = httpx.Timeout(timeout=timeout, connect=min(4.0, timeout))
    try:
        async with httpx.AsyncClient(timeout=client_timeout, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError, Exception) as exc:
        logger.warning(f"OpenFoodFacts v2 async fetch failed for {barcode}: {exc}")
        return None

    if not isinstance(data, dict):
        return None

    status = data.get("status")
    if status not in (1, "1", "success") and "product" not in data:
        return None

    return _normalize_product_dict(barcode, data.get("product") or {})
