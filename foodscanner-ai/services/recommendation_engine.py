from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd


logger = logging.getLogger(__name__)

INDIAN_DATASET_PATH = Path(__file__).resolve().parents[1] / "datasets" / "indian_foods" / "indian_packaged_foods.csv"

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "chips_and_snacks": [
        "chip", "chips", "wafer", "wafers", "crisp", "crisps", "namkeen", "bhujia",
        "sev", "snack", "snacks", "puff", "puffs", "popcorn", "kurkure", "mixture",
        "chanachur", "gathiya", "peanut", "peanuts", "cashew", "almond", "makhana",
    ],
    "dairy": [
        "milk", "paneer", "butter", "cheese", "dahi", "curd", "lassi", "buttermilk",
        "chaas", "ghee", "yogurt", "yoghurt", "shrikhand", "cream", "dairy",
    ],
    "biscuits_and_cookies": [
        "biscuit", "biscuits", "cookie", "cookies", "rusk", "cracker", "crackers",
        "cream biscuit", "digestive", "marie", "bourbon", "treat", "toast",
    ],
    "confectionery_and_sweets": [
        "chocolate", "chocolates", "sweet", "sweets", "mithai", "candy", "candies",
        "ice cream", "dessert", "halwa", "ladoo", "barfi", "jamun", "rasgulla",
        "toffee", "fudge", "chikki",
    ],
    "beverages": [
        "juice", "drink", "cola", "soda", "beverage", "beverages", "tea", "coffee",
        "shake", "squash", "syrup", "energy drink", "soft drink", "water",
    ],
    "instant_and_staples": [
        "noodle", "noodles", "maggi", "pasta", "soup", "oats", "cereal", "cereals",
        "muesli", "flakes", "corn flakes", "bread", "atta", "flour", "rice", "dal",
    ],
}


def _normalize_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(base: Optional[float], alt: Optional[float]) -> Optional[int]:
    """Calculate percentage reduction from base to alt. Positive means alt is lower."""
    if base is None or alt is None:
        return None
    if base <= 0:
        return None
    return int(round(((base - alt) / base) * 100))


def _percent_increase(base: Optional[float], alt: Optional[float]) -> Optional[int]:
    """Calculate percentage increase from base to alt. Positive means alt is higher."""
    if base is None or alt is None:
        return None
    if base <= 0:
        if alt > 0:
            return 100
        return None
    return int(round(((alt - base) / base) * 100))


def infer_food_category(product_name: str, nutrition_data: Optional[dict[str, Any]] = None) -> str:
    """Infer canonical food category from product name keywords and optional nutrition profile."""
    name_lower = (product_name or "").lower()

    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return cat

    if nutrition_data:
        cal = _to_float(nutrition_data.get("calories")) or 0.0
        sugar = _to_float(nutrition_data.get("sugar")) or 0.0
        fat = _to_float(nutrition_data.get("fat")) or 0.0
        carbs = _to_float(nutrition_data.get("carbs")) or 0.0

        if cal < 90 and carbs < 20 and fat < 3:
            return "beverages"
        if fat > 20 and carbs > 30 and sugar < 10:
            return "chips_and_snacks"
        if sugar > 30 and cal > 350:
            return "confectionery_and_sweets"
        if carbs > 40 and fat > 10 and sugar > 15:
            return "biscuits_and_cookies"

    return "general_food"


def _evaluate_candidate(
    base_name: str,
    base_nutr: dict[str, Any],
    base_cat: str,
    cand_name: str,
    cand_row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Evaluate whether candidate is a comparable, meaningfully healthier alternative."""
    if cand_name.strip().lower() == base_name.strip().lower():
        return None

    cand_cat = infer_food_category(cand_name, cand_row)
    if base_cat != "general_food" and cand_cat != base_cat:
        return None

    b_cal = _to_float(base_nutr.get("calories"))
    b_sugar = _to_float(base_nutr.get("sugar"))
    b_salt = _to_float(base_nutr.get("salt"))
    b_fat = _to_float(base_nutr.get("fat"))
    b_fiber = _to_float(base_nutr.get("fiber"))
    b_protein = _to_float(base_nutr.get("protein"))

    c_cal = _to_float(cand_row.get("calories"))
    c_sugar = _to_float(cand_row.get("sugar"))
    c_salt = _to_float(cand_row.get("salt"))
    c_fat = _to_float(cand_row.get("fat"))
    c_fiber = _to_float(cand_row.get("fiber"))
    c_protein = _to_float(cand_row.get("protein"))

    # Regression guards: candidate must not regress heavily on other key nutrients
    if b_sugar is not None and b_sugar <= 5.0 and c_sugar is not None and c_sugar > 15.0:
        return None
    if b_salt is not None and b_salt <= 0.5 and c_salt is not None and c_salt > 1.5:
        return None
    if b_cal is not None and b_cal > 50 and c_cal is not None and c_cal > b_cal * 1.25:
        return None
    if b_fat is not None and b_fat > 5.0 and c_fat is not None and c_fat > b_fat * 1.5:
        return None

    key_differences: dict[str, str] = {}
    reason_parts: list[str] = []
    improvement_score = 0.0
    meaningful_improvements = 0

    # 1. Sugar reduction
    sugar_red = _percent_change(b_sugar, c_sugar)
    if sugar_red is not None and sugar_red > 0:
        improvement_score += min(sugar_red, 100) * 0.4
        if sugar_red >= 15:
            meaningful_improvements += 1
            key_differences["sugar"] = f"-{sugar_red}%"
            reason_parts.append(f"{sugar_red}% less sugar")
    elif sugar_red is not None and sugar_red < -15:
        improvement_score -= min(abs(sugar_red), 100) * 0.3
        key_differences["sugar"] = f"+{abs(sugar_red)}%"

    # 2. Sodium / Salt reduction
    salt_red = _percent_change(b_salt, c_salt)
    if salt_red is not None and salt_red > 0:
        improvement_score += min(salt_red, 100) * 0.4
        if salt_red >= 15:
            meaningful_improvements += 1
            key_differences["salt"] = f"-{salt_red}%"
            reason_parts.append(f"{salt_red}% less sodium")
    elif salt_red is not None and salt_red < -15:
        improvement_score -= min(abs(salt_red), 100) * 0.3
        key_differences["salt"] = f"+{abs(salt_red)}%"

    # 3. Fat reduction
    fat_red = _percent_change(b_fat, c_fat)
    if fat_red is not None and fat_red > 0:
        improvement_score += min(fat_red, 100) * 0.3
        if fat_red >= 15:
            meaningful_improvements += 1
            key_differences["fat"] = f"-{fat_red}%"
            reason_parts.append(f"{fat_red}% less fat")
    elif fat_red is not None and fat_red < -15:
        improvement_score -= min(abs(fat_red), 100) * 0.25
        key_differences["fat"] = f"+{abs(fat_red)}%"

    # 4. Calorie reduction
    cal_red = _percent_change(b_cal, c_cal)
    if cal_red is not None and cal_red > 0:
        improvement_score += min(cal_red, 100) * 0.2
        if cal_red >= 15:
            meaningful_improvements += 1
            key_differences["calories"] = f"-{cal_red}%"
            reason_parts.append(f"{cal_red}% fewer calories")
    elif cal_red is not None and cal_red < -15:
        improvement_score -= min(abs(cal_red), 100) * 0.2
        key_differences["calories"] = f"+{abs(cal_red)}%"

    # 5. Fiber increase
    fiber_inc = _percent_increase(b_fiber, c_fiber)
    if fiber_inc is not None and fiber_inc >= 25:
        improvement_score += min(fiber_inc, 100) * 0.2
        meaningful_improvements += 1
        key_differences["fiber"] = f"+{fiber_inc}%"
        reason_parts.append(f"{fiber_inc}% more fiber")

    # 6. Protein increase
    prot_inc = _percent_increase(b_protein, c_protein)
    if prot_inc is not None and prot_inc >= 25:
        improvement_score += min(prot_inc, 100) * 0.2
        meaningful_improvements += 1
        key_differences["protein"] = f"+{prot_inc}%"
        reason_parts.append(f"{prot_inc}% more protein")

    if meaningful_improvements == 0 or improvement_score < 8.0:
        return None

    if not reason_parts:
        reason = "Better balanced nutritional profile"
    elif len(reason_parts) == 1:
        reason = reason_parts[0]
    elif len(reason_parts) == 2:
        reason = f"{reason_parts[0]} and {reason_parts[1]}"
    else:
        reason = ", ".join(reason_parts[:-1]) + f" and {reason_parts[-1]}"

    cand_nutr_for_score = {
        "calories": c_cal,
        "sugar": c_sugar,
        "salt": c_salt,
        "fat": c_fat,
        "protein": c_protein,
        "fiber": c_fiber,
        "carbs": _to_float(cand_row.get("carbs")),
    }
    from services.food_health_score import compute_food_health_score
    cand_health = compute_food_health_score(cand_nutr_for_score)
    cand_score = cand_health.get("health_score")
    cand_decision = cand_health.get("decision")

    cand_ns = cand_row.get("nutriscore")
    if not cand_ns:
        try:
            from ml_model.predict_nutriscore import calculate_heuristic_nutriscore
            cand_ns = calculate_heuristic_nutriscore(cand_nutr_for_score)
        except Exception:
            cand_ns = None

    cand_barcode = cand_row.get("barcode") or None

    return {
        "product_name": cand_name,
        "brand": _normalize_name(cand_row.get("brand")) or None,
        "barcode": cand_barcode,
        "category": cand_cat,
        "health_score": cand_score,
        "decision": cand_decision,
        "nutriscore": cand_ns,
        "nutrition": {
            "calories": c_cal,
            "sugar": c_sugar,
            "salt": c_salt,
            "fat": c_fat,
            "protein": c_protein,
            "fiber": c_fiber,
            "carbs": _to_float(cand_row.get("carbs")),
        },
        "reason": reason,
        "improvement_score": round(improvement_score, 1),
        "key_differences": key_differences,
        "source": "indian_dataset",
    }


def get_healthier_alternatives(
    product_name: str,
    nutrition_data: dict[str, Any],
    limit: int = 3,
    min_similarity: float = 70.0,
) -> list[dict[str, Any]]:
    """Find healthier alternatives for a given product based on category comparability and nutritional signals.
    
    Deterministic, explainable ranking. Returns structured data with key differences and reasons.
    Returns [] gracefully if no valid or healthier candidates exist.
    """
    name = _normalize_name(product_name)
    if not name or not isinstance(nutrition_data, dict):
        return []

    if not INDIAN_DATASET_PATH.exists():
        logger.warning(f"Indian foods dataset not found at {INDIAN_DATASET_PATH}")
        return []

    try:
        df = pd.read_csv(INDIAN_DATASET_PATH)
    except Exception as exc:
        logger.error(f"Failed to read dataset for alternatives: {exc}")
        return []

    if "product_name" not in df.columns:
        return []

    base_cat = infer_food_category(name, nutrition_data)
    candidates: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        cand_name = _normalize_name(row.get("product_name"))
        if not cand_name:
            continue

        cand_dict = row.to_dict()
        evaluated = _evaluate_candidate(
            base_name=name,
            base_nutr=nutrition_data,
            base_cat=base_cat,
            cand_name=cand_name,
            cand_row=cand_dict,
        )
        if evaluated is not None:
            candidates.append(evaluated)

    # Sort descending by improvement_score
    candidates.sort(key=lambda x: float(x.get("improvement_score", 0.0)), reverse=True)

    return candidates[: int(limit)]
