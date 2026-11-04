from __future__ import annotations

from typing import Any, Optional


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_flags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    return cleaned


def build_decision_reasons(product: dict, remaining_calories: float) -> list[str]:
    reasons: list[str] = []

    sugar = _to_float(product.get("sugar"))
    salt = _to_float(product.get("salt"))
    fat = _to_float(product.get("fat"))
    calories = _to_float(product.get("calories"))

    remaining = _to_float(remaining_calories)
    if calories is not None and remaining is not None and calories > remaining:
        reasons.append("exceeds remaining daily calorie budget")

    if sugar is not None and sugar > 20:
        reasons.append("high sugar content")
    if salt is not None and salt > 1.5:
        reasons.append("high sodium level")
    if fat is not None and fat > 25:
        reasons.append("high fat content")

    ingredient_analysis = product.get("ingredient_analysis") or {}
    ingredient_flags = _clean_flags(ingredient_analysis.get("flags"))
    if ingredient_flags:
        reasons.append("contains flagged ingredients")

    additive_analysis = product.get("additive_analysis") or {}
    additive_items = additive_analysis.get("additives") or []
    additive_codes = _clean_flags([a.get("code") if isinstance(a, dict) else a for a in additive_items])
    if additive_codes:
        reasons.append(f"contains risky additives: {', '.join(additive_codes)}")

    return reasons
