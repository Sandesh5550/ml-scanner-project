from __future__ import annotations

import re
from typing import Optional


HIGH_RISK_INGREDIENTS = {
    "hydrogenated vegetable oil",
    "partially hydrogenated oil",
    "high fructose corn syrup",
    "interesterified fat",
    "brominated vegetable oil",
}


MEDIUM_HIGH_RISK_INGREDIENTS = {
    "palm oil",
    "corn syrup",
    "artificial sweetener",
    "aspartame",
    "saccharin",
    "acesulfame",
    "sucralose",
    "sodium benzoate",
    "potassium bromate",
    "propyl gallate",
    "tbhq",
    "bha",
    "bht",
    "monosodium glutamate",
    "msg",
    "refined wheat flour",
    "maida",
    "vanaspati",
    "dalda",
    "refined palm olein",
    "artificial colour",
    "artificial color",
    "artificial flavour",
    "artificial flavor",
    "permitted emulsifier",
    "nature identical flavouring",
}


def analyze_ingredients(ingredients_text: Optional[str]) -> dict:
    text = (ingredients_text or "").lower()
    if not text.strip():
        return {"risk_level": "LOW", "flags": [], "high_risk_flags": []}

    matched_spans: list[tuple[int, int]] = []
    flags: list[str] = []
    high_risk_flags: list[str] = []

    # Sort all target items by length descending so longer phrases match first
    # e.g., "high fructose corn syrup" matches before "corn syrup",
    # and "monosodium glutamate" matches before "msg".
    all_targets = [(item, True) for item in HIGH_RISK_INGREDIENTS] + [
        (item, False) for item in MEDIUM_HIGH_RISK_INGREDIENTS
    ]
    all_targets.sort(key=lambda x: len(x[0]), reverse=True)

    for item, is_high in all_targets:
        # Match whole words / token boundaries using \b
        pattern = re.compile(r"\b" + re.escape(item) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span()
            # If this match overlaps an already matched longer ingredient span, skip it
            if any(s <= start and end <= e for s, e in matched_spans):
                continue
            matched_spans.append((start, end))
            if item not in flags:
                flags.append(item)
            if is_high and item not in high_risk_flags:
                high_risk_flags.append(item)

    if high_risk_flags:
        risk_level = "HIGH"
    else:
        medium_count = len(flags)
        if medium_count == 0:
            risk_level = "LOW"
        elif medium_count == 1:
            risk_level = "MEDIUM"
        else:
            risk_level = "HIGH"

    return {"risk_level": risk_level, "flags": flags, "high_risk_flags": high_risk_flags}
