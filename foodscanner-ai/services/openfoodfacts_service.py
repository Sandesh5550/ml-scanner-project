from __future__ import annotations

import logging
import os
from typing import Any, Optional

from services.off_client import (
    API_V2_URL_TEMPLATE,
    API_V3_URL_TEMPLATE,
    DEFAULT_TOTAL_TIMEOUT,
    DEFAULT_USER_AGENT,
    canonical_to_legacy_dict,
    get_default_off_client,
    normalize_off_to_canonical,
    to_optional_float,
)

logger = logging.getLogger(__name__)

# Legacy Constants Preserved for Backward Compatibility
API_URL_TEMPLATE = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
API_V2_URL_TEMPLATE = API_V2_URL_TEMPLATE
USER_AGENT = DEFAULT_USER_AGENT
DEFAULT_TIMEOUT = DEFAULT_TOTAL_TIMEOUT


def _to_float(value: Any) -> Optional[float]:
    """Legacy helper: safely convert numeric string or number to float."""
    return to_optional_float(value)


def _normalize_product_dict(barcode: str, product: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Legacy helper: normalize raw OpenFoodFacts dictionary to legacy flat format."""
    canonical = normalize_off_to_canonical(barcode, product)
    if canonical is None:
        return None
    return canonical_to_legacy_dict(canonical)


def fetch_product_by_barcode(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode using resilient OpenFoodFactsClient (v3 with v2 fallback)."""
    client = get_default_off_client()
    return client.get_legacy_dict(barcode, timeout=timeout)


async def fetch_product_by_barcode_async(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode asynchronously using resilient OpenFoodFactsClient."""
    client = get_default_off_client()
    return await client.get_legacy_dict_async(barcode, timeout=timeout)


def fetch_product_by_barcode_v2(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v2 API."""
    client = get_default_off_client()
    return client.get_legacy_dict(barcode, force_version="v2", timeout=timeout)


async def fetch_product_by_barcode_v2_async(barcode: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict[str, Any]]:
    """Fetch and normalize product by barcode from OpenFoodFacts v2 API asynchronously."""
    client = get_default_off_client()
    return await client.get_legacy_dict_async(barcode, force_version="v2", timeout=timeout)
