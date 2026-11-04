from __future__ import annotations

from typing import Any

from services.food_health_score import (
    _to_float,
    _sugar_penalty,
    _salt_penalty,
    _fat_penalty,
    _sat_fat_penalty,
    _calories_penalty,
    _protein_bonus,
    _fiber_bonus,
    _nutriscore_bonus,
    compute_food_health_score,
    compute_diet_aware_score,
)


def _explain_penalty(name: str, value: float, func, reason_template: str) -> dict[str, Any]:
    impact = func(value)
    return {
        "factor": name,
        "value": f"{value}g",
        "impact": impact,
        "reason": reason_template.format(v=value) + f" → {impact:+d} points",
    }


def _explain_bonus(name: str, value: float, func, reason_template: str) -> dict[str, Any]:
    impact = func(value)
    return {
        "factor": name,
        "value": f"{value}g",
        "impact": impact,
        "reason": reason_template.format(v=value) + f" → {impact:+d} bonus points",
    }


def _explain_nutriscore(nutriscore: str | None) -> dict[str, Any]:
    impact = _nutriscore_bonus(nutriscore)
    return {
        "factor": "NutriScore",
        "value": nutriscore or "N/A",
        "impact": impact,
        "reason": f"NutriScore {nutriscore or 'N/A'} → {impact:+d} points",
    }


def _explain_risk(name: str, analysis: dict[str, Any]) -> dict[str, Any]:
    risk = str(analysis.get("risk_level") or "").upper()
    if risk == "LOW":
        impact = 0
    elif risk == "MEDIUM":
        impact = -10
    elif risk == "HIGH":
        impact = -25
    else:
        impact = 0
        risk = "UNKNOWN"
    details = analysis.get("high_risk_flags") or []
    details_str = ", ".join(details) if details else ""
    reason = f"{name} {risk} risk"
    if details_str:
        reason += f": {details_str}"
    reason += f" → {impact:+d} points"
    return {
        "factor": name,
        "value": f"{risk} risk",
        "impact": impact,
        "reason": reason,
    }


def _explain_diet_adjustment(product: dict[str, Any], diet_type: str | None) -> dict[str, Any] | None:
    if not diet_type:
        return None
    dt = diet_type.strip().lower()
    sugar = _to_float(product.get("sugar"))
    carbs = _to_float(product.get("carbs"))
    ingredients = str(product.get("ingredients") or "").lower()
    salt = _to_float(product.get("salt"))
    impact = 0
    reason_parts = []
    if dt == "diabetic":
        impact += _sugar_penalty(sugar)  # double penalty
        if carbs is not None and carbs > 30:
            impact -= 15
            reason_parts.append(f"High carbs ({carbs}g) for diabetic diet")
        reason_parts.append("Sugar penalty doubled for diabetic diet")
    elif dt == "vegan":
        if any(k in ingredients for k in ["milk", "egg", "meat", "chicken", "fish", "butter", "cheese", "honey"]):
            impact -= 30
            reason_parts.append("Contains non-vegan ingredients")
        else:
            reason_parts.append("Compatible with vegan diet")
    elif dt == "vegetarian":
        if any(k in ingredients for k in ["meat", "chicken", "fish", "pork", "beef", "lamb"]):
            impact -= 30
            reason_parts.append("Contains non-vegetarian meat")
        else:
            reason_parts.append("Compatible with vegetarian diet")
    elif dt == "low_sodium":
        impact += _salt_penalty(salt)  # triple penalty
        reason_parts.append("Salt penalty tripled for low-sodium diet")
    else:
        return None
    reason = "; ".join(reason_parts) + f" → {impact:+d}"
    return {
        "factor": "Diet adjustment",
        "value": diet_type,
        "impact": impact,
        "reason": reason,
    }


def explain_score(product: dict[str, Any], diet_type: str | None) -> dict[str, Any]:
    product_name = product.get("product_name", "Unknown")
    sugar = _to_float(product.get("sugar")) or 0
    salt = _to_float(product.get("salt")) or 0
    fat = _to_float(product.get("fat")) or 0
    sat_fat = _to_float(product.get("saturated_fat")) or 0
    calories = _to_float(product.get("calories")) or 0
    protein = _to_float(product.get("protein")) or 0
    fiber = _to_float(product.get("fiber")) or 0

    steps: list[dict[str, Any]] = []

    # Base penalties
    steps.append(_explain_penalty("Sugar", sugar, _sugar_penalty, "Sugar {v}g"))
    steps.append(_explain_penalty("Salt", salt, _salt_penalty, "Salt {v}g"))
    steps.append(_explain_penalty("Fat", fat, _fat_penalty, "Fat {v}g"))
    steps.append(_explain_penalty("Saturated fat", sat_fat, _sat_fat_penalty, "Saturated fat {v}g"))
    steps.append(_explain_penalty("Calories", calories, _calories_penalty, "Calories {v} kcal"))
    # Bonuses
    steps.append(_explain_bonus("Protein", protein, _protein_bonus, "Protein {v}g"))
    steps.append(_explain_bonus("Fiber", fiber, _fiber_bonus, "Fiber {v}g"))
    # Ingredient/additive risk
    ingredient_analysis = product.get("ingredient_analysis") or {}
    steps.append(_explain_risk("Ingredients", ingredient_analysis))
    additive_analysis = product.get("additive_analysis") or {}
    steps.append(_explain_risk("Additives", additive_analysis))
    # NutriScore
    steps.append(_explain_nutriscore(product.get("nutriscore")))
    # Diet adjustment
    diet_step = _explain_diet_adjustment(product, diet_type)
    if diet_step:
        steps.append(diet_step)

    # Compute final score and decision
    if diet_type:
        result = compute_diet_aware_score(product, diet_type)
    else:
        result = compute_food_health_score(product)
    final_score = result["health_score"]
    final_decision = result["decision"]
    diet_note = result.get("diet_note")

    # Build calculation string
    parts = ["100"]
    for step in steps:
        parts.append(f"{step['impact']:+d}")
    calc_str = " ".join(parts) + f" = {final_score}"

    positive_factors = [s for s in steps if s.get("impact", 0) > 0]
    negative_factors = [s for s in steps if s.get("impact", 0) < 0]
    neutral_factors = [s for s in steps if s.get("impact", 0) == 0]

    pos_count = len(positive_factors)
    neg_count = len(negative_factors)
    summary = (
        f"Base score: 100. Evaluated {len(steps)} nutritional and ingredient criteria. "
        f"{neg_count} deduction(s) and {pos_count} bonus(es) result in a score of {final_score}/100 ({final_decision})."
    )

    return {
        "product_name": product_name,
        "final_score": final_score,
        "final_decision": final_decision,
        "base_score": 100,
        "steps": steps,
        "positive_factors": positive_factors,
        "negative_factors": negative_factors,
        "neutral_factors": neutral_factors,
        "summary": summary,
        "score_calculation": calc_str,
        "diet_note": diet_note,
        "threshold_info": {
            "SAFE": "score >= 70",
            "MODERATE": "score >= 45",
            "AVOID": "score < 45",
        },
    }
