from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Union

ALLERGEN_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "peanuts": {
        "display_name": "Peanuts",
        "keywords": ["peanut", "groundnut", "arachis", "monkey nut"],
        "severity": "HIGH",
    },
    "tree_nuts": {
        "display_name": "Tree Nuts",
        "keywords": ["almond", "cashew", "walnut", "hazelnut", "pistachio", "pecan", "macadamia", "brazil nut", "chestnut"],
        "severity": "HIGH",
    },
    "milk": {
        "display_name": "Milk & Dairy",
        "keywords": ["milk", "dairy", "cheese", "butter", "whey", "casein", "lactose", "curd", "paneer", "yogurt", "cream", "ghee", "milk solids", "condensed milk"],
        "severity": "MEDIUM",
    },
    "eggs": {
        "display_name": "Eggs",
        "keywords": ["egg", "eggs", "albumin", "ovalbumin", "yolk", "egg white", "egg powder", "whole egg"],
        "severity": "MEDIUM",
    },
    "wheat_gluten": {
        "display_name": "Wheat & Gluten",
        "keywords": ["wheat", "gluten", "maida", "barley", "rye", "spelt", "semolina", "atta", "wheat flour", "malt"],
        "severity": "MEDIUM",
    },
    "soy": {
        "display_name": "Soy / Soybeans",
        "keywords": ["soy", "soya", "soybean", "edamame", "tofu", "soy lecithin", "hydrolyzed soy", "soy protein"],
        "severity": "MEDIUM",
    },
    "fish": {
        "display_name": "Fish",
        "keywords": ["fish", "salmon", "tuna", "cod", "anchovy", "sardine", "tilapia", "haddock", "trout"],
        "severity": "HIGH",
    },
    "shellfish": {
        "display_name": "Crustaceans & Shellfish",
        "keywords": ["shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop", "squid", "crayfish"],
        "severity": "HIGH",
    },
    "sesame": {
        "display_name": "Sesame Seeds",
        "keywords": ["sesame", "til", "tahini", "gingelly"],
        "severity": "MEDIUM",
    },
    "mustard": {
        "display_name": "Mustard",
        "keywords": ["mustard", "sarson", "rai"],
        "severity": "MEDIUM",
    },
}


def detect_allergens(ingredients_text: Optional[str]) -> List[str]:
    """Detect presence of major food allergens in product ingredients using word-boundary matching."""
    if not ingredients_text or not isinstance(ingredients_text, str):
        return []

    text_lower = ingredients_text.lower()
    detected: List[str] = []

    for allergen_key, defn in ALLERGEN_DEFINITIONS.items():
        for kw in defn["keywords"]:
            # Match whole words / token boundaries, supporting singular and plural forms
            pattern = re.compile(r"\b" + re.escape(kw) + r"(?:s|es)?\b", re.IGNORECASE)
            if pattern.search(text_lower):
                if allergen_key not in detected:
                    detected.append(allergen_key)
                break

    return detected


def check_allergen_warnings(
    detected_allergens: List[str],
    user_allergens: Optional[Union[List[str], str]] = None,
) -> List[Dict[str, str]]:
    """Compare detected allergens against user's specific allergy profile and generate safety warnings."""
    if not detected_allergens:
        return []

    user_set: Set[str] = set()
    if isinstance(user_allergens, str):
        for item in user_allergens.split(","):
            cleaned = item.strip().lower()
            if cleaned:
                user_set.add(cleaned)
    elif isinstance(user_allergens, (list, tuple, set)):
        user_set = {str(item).strip().lower() for item in user_allergens if str(item).strip()}

    warnings: List[Dict[str, str]] = []
    for allergen_key in detected_allergens:
        defn = ALLERGEN_DEFINITIONS.get(allergen_key, {})
        display = defn.get("display_name", allergen_key.replace("_", " ").title())
        is_user_allergy = allergen_key in user_set

        if is_user_allergy:
            warnings.append(
                {
                    "allergen": allergen_key,
                    "display_name": display,
                    "severity": "CRITICAL",
                    "user_match": True,
                    "warning": f"ALLERGEN ALERT: Contains {display}, which matches your declared allergy profile! Exercise extreme caution.",
                }
            )
        elif not user_set:
            # Informational notice if user hasn't declared specific allergies
            warnings.append(
                {
                    "allergen": allergen_key,
                    "display_name": display,
                    "severity": defn.get("severity", "MEDIUM"),
                    "user_match": False,
                    "warning": f"Contains {display}.",
                }
            )

    return warnings
