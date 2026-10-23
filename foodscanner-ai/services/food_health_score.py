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


def _sugar_penalty(sugar: Optional[float]) -> int:
    if sugar is None:
        return 0
    if sugar <= 5:
        return 0
    if sugar <= 10:
        return -10
    if sugar <= 20:
        return -20
    return -35


def _salt_penalty(salt: Optional[float]) -> int:
    if salt is None:
        return 0
    if salt <= 0.3:
        return 0
    if salt <= 1.0:
        return -10
    if salt <= 1.5:
        return -20
    return -30


def _fat_penalty(fat: Optional[float]) -> int:
    if fat is None:
        return 0
    if fat <= 3:
        return 0
    if fat <= 17.5:
        return -10
    if fat <= 25:
        return -20
    return -30


def _sat_fat_penalty(sat_fat: Optional[float]) -> int:
    if sat_fat is None:
        return 0
    return -10 if sat_fat > 5 else 0


def _calories_penalty(calories: Optional[float]) -> int:
    if calories is None:
        return 0
    if calories < 200:
        return 0
    if calories <= 400:
        return -5
    return -15


def _protein_bonus(protein: Optional[float]) -> int:
    if protein is None:
        return 0
    if protein > 10:
        return 15
    if protein > 5:
        return 8
    return 0


def _fiber_bonus(fiber: Optional[float]) -> int:
    if fiber is None:
        return 0
    if fiber > 6:
        return 15
    if fiber > 3:
        return 8
    return 0


def _ingredient_risk_penalty(ingredient_analysis: dict[str, Any]) -> int:
    risk = str(ingredient_analysis.get("risk_level") or "").upper()
    if risk == "LOW":
        return 0
    if risk == "MEDIUM":
        return -10
    if risk == "HIGH":
        return -25
    return 0


def _additive_risk_penalty(additive_analysis: dict[str, Any]) -> int:
    risk = str(additive_analysis.get("risk_level") or "").upper()
    if risk == "LOW":
        return 0
    if risk == "MEDIUM":
        return -10
    if risk == "HIGH":
        return -25
    return 0


def _nutriscore_bonus(nutriscore: Optional[str]) -> int:
    if not nutriscore:
        return 0
    ns = str(nutriscore).strip().upper()
    if ns == "A":
        return 15
    if ns == "B":
        return 8
    if ns == "C":
        return 0
    if ns == "D":
        return -10
    if ns == "E":
        return -20
    return 0


def compute_food_health_score(product: dict) -> dict:
    score = 100

    sugar = _to_float(product.get("sugar"))
    salt = _to_float(product.get("salt"))
    fat = _to_float(product.get("fat"))
    sat_fat = _to_float(product.get("saturated_fat"))
    calories = _to_float(product.get("calories"))
    protein = _to_float(product.get("protein"))
    fiber = _to_float(product.get("fiber"))

    score += _sugar_penalty(sugar)
    score += _salt_penalty(salt)
    score += _fat_penalty(fat)
    score += _sat_fat_penalty(sat_fat)
    score += _calories_penalty(calories)
    score += _protein_bonus(protein)
    score += _fiber_bonus(fiber)

    ingredient_analysis = product.get("ingredient_analysis") or {}
    score += _ingredient_risk_penalty(ingredient_analysis)

    additive_analysis = product.get("additive_analysis") or {}
    score += _additive_risk_penalty(additive_analysis)

    score += _nutriscore_bonus(product.get("nutriscore"))

    score = max(0, min(100, score))

    if score >= 70:
        decision = "SAFE"
    elif score >= 45:
        decision = "MODERATE"
    else:
        decision = "AVOID"

    reasons: list[str] = []
    if sugar is not None and sugar > 20:
        reasons.append("High sugar")
    if salt is not None and salt > 1.5:
        reasons.append("High salt")
    if fat is not None and fat > 25:
        reasons.append("High fat")
    if sat_fat is not None and sat_fat > 5:
        reasons.append("High saturated fat")
    if calories is not None and calories > 400:
        reasons.append("High calories")
    if protein is not None and protein > 10:
        reasons.append("High protein")
    if fiber is not None and fiber > 6:
        reasons.append("High fiber")
    if ingredient_analysis.get("risk_level") in {"MEDIUM", "HIGH"}:
        reasons.append(f"Ingredients risk {ingredient_analysis.get('risk_level')}")
    if additive_analysis.get("risk_level") in {"MEDIUM", "HIGH"}:
        reasons.append(f"Additives risk {additive_analysis.get('risk_level')}")
    if product.get("nutriscore"):
        reasons.append(f"NutriScore {product.get('nutriscore')}")

    reason = ", ".join(reasons) if reasons else "No major risk factors detected"

    return {"health_score": score, "decision": decision, "reason": reason}


def _contains_any(text: str | None, keywords: list[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(k in lower for k in keywords)


def compute_diet_aware_score(product: dict, diet_type: str | None) -> dict:
    base = compute_food_health_score(product)
    score = base["health_score"]
    notes: list[str] = []
    diabetic_no_safe = False
    diabetic_force_avoid = False
    low_sodium_no_safe = False
    vegan_force_avoid = False
    vegetarian_force_avoid = False

    if not diet_type:
        return base

    dt = diet_type.strip().lower()

    if dt == "diabetic":
        sugar = _to_float(product.get("sugar"))
        carbs = _to_float(product.get("carbs"))
        score += _sugar_penalty(sugar)  # double penalty
        if sugar is not None and sugar > 15:
            diabetic_force_avoid = True
            notes.append("High sugar (>15g/100g): AVOID for diabetic diet")
        elif sugar is not None and sugar > 5:
            diabetic_no_safe = True
            notes.append("Sugar > 5g: not SAFE for diabetic diet")
        if carbs is not None and carbs > 30:
            score -= 15
            notes.append("High carbs for diabetic diet")
        ingredients = str(product.get("ingredients") or "").lower()
        if _contains_any(ingredients, ["high fructose corn syrup", "corn syrup", "maltodextrin", "dextrose", "glucose syrup"]):
            score -= 15
            notes.append("Contains rapid-spiking refined syrups/sweeteners")
        notes.append("Sugar penalty doubled for diabetic diet")

    elif dt == "vegan":
        ingredients = str(product.get("ingredients") or "").lower()
        non_vegan = [
            "milk", "dairy", "egg", "meat", "chicken", "fish", "butter", "cheese",
            "honey", "whey", "casein", "gelatin", "carmine", "lard", "tallow",
            "yogurt", "ghee", "curd", "paneer", "shellfish", "prawn", "crab"
        ]
        if _contains_any(ingredients, non_vegan):
            score -= 30
            vegan_force_avoid = True
            notes.append("Contains non-vegan animal-derived ingredients: AVOID for vegan diet")
        else:
            notes.append("Compatible with vegan diet")

    elif dt == "vegetarian":
        ingredients = str(product.get("ingredients") or "").lower()
        non_veg = [
            "meat", "chicken", "fish", "pork", "beef", "lamb", "gelatin", "lard",
            "tallow", "shrimp", "prawn", "crab", "lobster", "bacon", "poultry",
            "seafood", "anchovy"
        ]
        if _contains_any(ingredients, non_veg):
            score -= 30
            vegetarian_force_avoid = True
            notes.append("Contains non-vegetarian meat/gelatin: AVOID for vegetarian diet")
        else:
            notes.append("Compatible with vegetarian diet")

    elif dt == "low_sodium":
        salt = _to_float(product.get("salt"))
        score += 2 * _salt_penalty(salt)  # triple penalty (1x in base + 2x additional = 3x total)
        if salt is not None and salt > 1.5:
            low_sodium_no_safe = True
            notes.append("High salt (>1.5g/100g) exceeds low-sodium guidelines")
        notes.append("Salt penalty tripled for low-sodium diet")

    else:
        # unknown diet: use standard scoring
        return base

    score = max(0, min(100, score))

    if score >= 70:
        decision = "SAFE"
    elif score >= 45:
        decision = "MODERATE"
    else:
        decision = "AVOID"

    if diabetic_force_avoid or vegan_force_avoid or vegetarian_force_avoid:
        decision = "AVOID"
    elif diabetic_no_safe and decision == "SAFE":
        decision = "MODERATE"
    elif low_sodium_no_safe and decision == "SAFE":
        decision = "MODERATE"

    diet_note = "; ".join(notes) if notes else None

    return {"health_score": score, "decision": decision, "reason": base["reason"], "diet_note": diet_note}
