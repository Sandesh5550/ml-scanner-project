from __future__ import annotations

from typing import Any, Optional
from services.allergen_service import detect_allergens, check_allergen_warnings


def calculate_personalized_calorie_estimate(
    age: Optional[int],
    weight_kg: Optional[float],
    height_cm: Optional[float],
    goal_type: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Calculate personalized calorie estimate using Mifflin-St Jeor equation.

    Requires age, weight (kg), and height (cm). If any are missing or invalid, returns None.
    Documents assumptions and provides an explicit disclaimer that this is not medical advice.
    """
    if age is None or weight_kg is None or height_cm is None:
        return None
    try:
        a = float(age)
        w = float(weight_kg)
        h = float(height_cm)
        if a <= 0 or w <= 0 or h <= 0 or a > 120 or w > 400 or h > 300:
            return None
    except (ValueError, TypeError):
        return None

    # Baseline Mifflin-St Jeor: 10*weight + 6.25*height - 5*age - 78 (neutral offset)
    bmr = (10.0 * w) + (6.25 * h) - (5.0 * a) - 78.0
    # Standard moderate daily activity factor: 1.35
    tdee = bmr * 1.35

    goal = (goal_type or "").strip().lower()
    if goal in {"lose_weight", "weight_loss"}:
        recommended = max(1200.0, tdee - 450.0)
        goal_note = "Caloric deficit (-450 kcal) targeted for sustainable fat loss"
    elif goal in {"weight_gain"}:
        recommended = tdee + 400.0
        goal_note = "Caloric surplus (+400 kcal) targeted for lean mass gain"
    elif goal in {"build_muscle", "muscle_gain"}:
        recommended = tdee + 250.0
        goal_note = "Slight surplus (+250 kcal) to support muscle protein synthesis"
    else:
        recommended = tdee
        goal_note = "Maintenance caloric target"

    return {
        "bmr": round(bmr, 0),
        "tdee": round(tdee, 0),
        "recommended_calories": int(round(recommended / 50.0) * 50),
        "goal_note": goal_note,
        "disclaimer": (
            "Nutritional guideline based on the Mifflin-St Jeor equation (moderate activity). "
            "This is an informational estimate and does not constitute medical advice."
        ),
    }


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_personalized_analysis(
    product: dict[str, Any],
    user: Any,
    remaining_calories: Optional[float] = None,
    today_calories_consumed: Optional[float] = None,
) -> dict[str, Any]:
    """Generate deterministic, nutritional and goal-oriented product guidance.

    Does not give medical diagnosis or prescribe treatments.
    Gracefully handles missing profile fields without inventing values.
    """
    if user is None:
        user_diet = None
        user_goal = None
        user_allergens = None
        daily_limit = 2000.0
        age = None
        weight = None
        height = None
    elif isinstance(user, dict):
        user_diet = (user.get("diet_type") or "").strip().lower() or None
        user_goal = (user.get("goal_type") or "").strip().lower() or None
        user_allergens = user.get("allergens")
        daily_limit = float(user.get("daily_calorie_limit") or 2000)
        age = user.get("age")
        weight = _to_float(user.get("weight"))
        height = _to_float(user.get("height"))
    else:
        user_diet = (getattr(user, "diet_type", None) or "").strip().lower() or None
        user_goal = (getattr(user, "goal_type", None) or "").strip().lower() or None
        user_allergens = getattr(user, "allergens", None)
        daily_limit = float(getattr(user, "daily_calorie_limit", None) or 2000)
        age = getattr(user, "age", None)
        weight = _to_float(getattr(user, "weight", None))
        height = _to_float(getattr(user, "height", None))


    cal = _to_float(product.get("calories")) or 0.0
    sugar = _to_float(product.get("sugar"))
    salt = _to_float(product.get("salt"))
    fat = _to_float(product.get("fat"))
    protein = _to_float(product.get("protein"))
    fiber = _to_float(product.get("fiber"))
    carbs = _to_float(product.get("carbs"))

    # 1. Calorie Budget Evaluation
    rem_cal = remaining_calories
    consumed_cal = today_calories_consumed

    fits_budget = None
    budget_message = None
    if rem_cal is not None:
        fits_budget = cal <= rem_cal
        if fits_budget:
            budget_message = f"Fits within your remaining daily calorie budget ({rem_cal:.0f} kcal remaining)."
        else:
            budget_message = (
                f"Exceeds remaining daily calorie budget by {cal - rem_cal:.0f} kcal "
                f"({rem_cal:.0f} kcal remaining vs {cal:.0f} kcal in product)."
            )

    # 2. Goal-Specific Nutritional Guidance
    goal_signals: list[str] = []
    goal_recommendations: list[str] = []

    if user_goal in {"lose_weight", "weight_loss"}:
        if cal > 350:
            goal_signals.append(f"High caloric density ({cal:.0f} kcal/100g) for a weight loss goal.")
        elif cal < 150:
            goal_signals.append(f"Low calorie density ({cal:.0f} kcal/100g) aligns well with weight loss.")

        if sugar is not None and sugar > 15:
            goal_signals.append(f"High sugar content ({sugar}g) can spike insulin and hinder fat loss.")
        if fiber is not None and fiber >= 3.0:
            goal_signals.append(f"Good fiber content ({fiber}g) promotes fullness and satiety.")

        goal_recommendations.append("Prioritize foods high in dietary fiber and lean protein to sustain satiety.")

    elif user_goal in {"weight_gain"}:
        if cal >= 300:
            goal_signals.append(f"Good caloric density ({cal:.0f} kcal/100g) supports achieving a caloric surplus.")
        else:
            goal_signals.append(f"Relatively low calorie density ({cal:.0f} kcal/100g) for a caloric surplus.")

        if protein is not None and protein >= 8:
            goal_signals.append(f"Provides {protein}g protein to ensure lean mass gain during surplus.")

        goal_recommendations.append("Pair calorie-dense foods with adequate protein rather than excess refined sugar.")

    elif user_goal in {"build_muscle", "muscle_gain", "fitness"}:
        if protein is not None:
            if protein >= 15:
                goal_signals.append(f"High protein content ({protein}g/100g) strongly supports muscle protein synthesis.")
            elif protein >= 8:
                goal_signals.append(f"Moderate protein content ({protein}g/100g).")
            else:
                goal_signals.append(f"Low protein content ({protein}g/100g) for a muscle building target.")
        else:
            goal_signals.append("Protein data not available on label.")

        if cal > 0 and protein is not None and protein > 0:
            protein_cal_pct = (protein * 4.0 / cal) * 100
            if protein_cal_pct >= 20:
                goal_signals.append(f"{protein_cal_pct:.0f}% of calories come from protein.")

        goal_recommendations.append("Combine protein-rich choices with regular progressive resistance exercise.")

    elif user_goal in {"control_sugar"}:
        if sugar is not None:
            if sugar <= 5:
                goal_signals.append(f"Low sugar ({sugar}g/100g) aligns with your sugar control target.")
            elif sugar <= 15:
                goal_signals.append(f"Moderate sugar ({sugar}g/100g).")
            else:
                goal_signals.append(f"High sugar ({sugar}g/100g) — exceeds recommended daily thresholds.")
        goal_recommendations.append("Check ingredient lists for hidden syrups, maltodextrin, and artificial sweeteners.")

    elif user_goal in {"reduce_sodium"}:
        if salt is not None:
            if salt <= 0.3:
                goal_signals.append(f"Low sodium/salt ({salt}g/100g) fits sodium reduction goal.")
            elif salt <= 1.0:
                goal_signals.append(f"Moderate salt content ({salt}g/100g).")
            else:
                goal_signals.append(f"High salt content ({salt}g/100g) — watch total daily sodium intake.")
        goal_recommendations.append("Favor fresh ingredients and low-sodium seasonings like herbs and lemon.")

    elif user_goal in {"eat_clean", "clean_eating"}:
        add_risk = str((product.get("additive_analysis") or {}).get("risk_level") or "").upper()
        if add_risk in {"MEDIUM", "HIGH"}:
            goal_signals.append(f"Contains additives flagged as {add_risk} risk.")
        else:
            goal_signals.append("No high-risk additives detected.")
        goal_recommendations.append("Choose minimally processed products with short, recognizable ingredient lists.")

    elif user_goal in {"maintenance", "healthy_lifestyle"}:
        goal_signals.append(f"Provides {cal:.0f} kcal towards daily limit of {daily_limit:.0f} kcal.")
        goal_recommendations.append("Maintain an even balance of protein, complex carbohydrates, and healthy fats.")

    # 3. Diet Alignment
    diet_compatibility = None
    diet_note = product.get("diet_note")

    if user_diet:
        dt = user_diet.lower()
        if dt == "diabetic":
            if sugar is not None and sugar > 5:
                diet_compatibility = "CAUTION"
            else:
                diet_compatibility = "COMPATIBLE"
        elif dt in {"vegan", "vegetarian"}:
            ing_text = str(product.get("ingredients") or "").lower()
            if dt == "vegan" and any(k in ing_text for k in ["milk", "egg", "meat", "chicken", "fish", "butter", "cheese", "honey"]):
                diet_compatibility = "INCOMPATIBLE"
            elif dt == "vegetarian" and any(k in ing_text for k in ["meat", "chicken", "fish", "pork", "beef", "lamb"]):
                diet_compatibility = "INCOMPATIBLE"
            else:
                diet_compatibility = "COMPATIBLE"
        elif dt == "low_sodium":
            if salt is not None and salt > 1.0:
                diet_compatibility = "CAUTION"
            else:
                diet_compatibility = "COMPATIBLE"

    # 4. Allergen Analysis
    detected_allergens = detect_allergens(product.get("ingredients"))
    allergen_warnings = check_allergen_warnings(detected_allergens, user_allergens)

    # 5. Personalized Calorie Target Estimation
    calorie_estimate = calculate_personalized_calorie_estimate(age, weight, height, user_goal)

    return {
        "user_profile": {
            "diet_type": user_diet,
            "goal_type": user_goal,
            "allergens": user_allergens,
            "daily_calorie_limit": daily_limit,
            "age": age,
            "weight": weight,
            "height": height,
        },
        "diet_alignment": {
            "diet_type": user_diet,
            "compatibility": diet_compatibility,
            "diet_note": diet_note,
        },
        "goal_alignment": {
            "goal_type": user_goal,
            "signals": goal_signals,
            "recommendations": goal_recommendations,
        },
        "calorie_budget": {
            "daily_limit": daily_limit,
            "consumed_today": consumed_cal,
            "remaining_before": rem_cal,
            "product_calories": cal,
            "remaining_after": max(0.0, rem_cal - cal) if rem_cal is not None else None,
            "fits_budget": fits_budget,
            "message": budget_message,
        },
        "allergen_warnings": allergen_warnings,
        "detected_allergens": detected_allergens,
        "calorie_estimate": calorie_estimate,
    }
