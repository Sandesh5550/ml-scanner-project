from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from schemas.product import (
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    Nutrition100g,
    ScoreRange,
    calculate_ingredients_completeness,
    calculate_nutrition_completeness,
    calculate_salt_from_sodium,
    resolve_effective_salt,
)
from services.additive_analyzer import analyze_additives
from services.ingredient_analyzer import analyze_ingredients

logger = logging.getLogger(__name__)

# Mandatory nutrition dimensions
MANDATORY_DIMENSIONS = ("calories", "sugar", "fat", "salt")

# Worst-case regulatory penalties for missing mandatory nutrients in partial assessment
WORST_CASE_PENALTIES: dict[str, int] = {
    "sugar": -35,
    "salt": -30,
    "fat": -30,
    "calories": -15,
}

# Non-vegan and non-vegetarian ingredient keywords
NON_VEGAN_KEYWORDS = [
    "milk", "dairy", "egg", "meat", "chicken", "fish", "butter", "cheese",
    "honey", "whey", "casein", "gelatin", "carmine", "lard", "tallow",
    "yogurt", "ghee", "curd", "paneer", "shellfish", "prawn", "crab",
]

NON_VEG_KEYWORDS = [
    "meat", "chicken", "fish", "pork", "beef", "lamb", "gelatin", "lard",
    "tallow", "shrimp", "prawn", "crab", "lobster", "bacon", "poultry",
    "seafood", "anchovy",
]

REFINED_SYRUPS_KEYWORDS = [
    "high fructose corn syrup", "corn syrup", "maltodextrin", "dextrose", "glucose syrup"
]


def _to_float(value: Any) -> Optional[float]:
    """Safely convert value to float, handling strings, None, and empty text."""
    try:
        if value is None:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
            # Handle unit suffixes if present
            if val_str.lower().endswith("kcal"):
                val_str = val_str[:-4].strip()
            elif val_str.lower().endswith("mg"):
                return float(val_str[:-2].strip()) / 1000.0
            elif val_str.lower().endswith("g"):
                val_str = val_str[:-1].strip()
            return float(val_str)
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_nutrition_float(value: Any) -> Optional[float]:
    """Convert value to float and reject negative, NaN, infinite, or malformed inputs."""
    val = _to_float(value)
    if val is None:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    if val < 0.0:
        return None
    return val


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


def _contains_any(text: Optional[str], keywords: list[str]) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(k in lower for k in keywords)


class ScoringAssessment(BaseModel):
    """Authoritative structured output of the Universal Scoring Engine."""
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    product_name: str = "Unknown Product"
    assessment_status: AssessmentStatus
    health_score: Optional[int] = None
    score_range: Optional[ScoreRange] = None
    decision: str  # SAFE, MODERATE, AVOID, INCOMPLETE_DATA, INSUFFICIENT_DATA
    final_decision: str
    reasons: list[str] = Field(default_factory=list)
    reason: str = ""
    overall_confidence: DataConfidence
    is_complete: bool = False
    missing_mandatory_nutrients: list[str] = Field(default_factory=list)
    diet_note: Optional[str] = None
    is_diet_assessable: bool = True
    nutriscore: Optional[str] = None
    predicted_nutriscore: Optional[str] = None
    is_nutriscore_predicted: bool = False
    ingredient_analysis: Optional[dict[str, Any]] = None
    additive_analysis: Optional[dict[str, Any]] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    positive_factors: list[dict[str, Any]] = Field(default_factory=list)
    negative_factors: list[dict[str, Any]] = Field(default_factory=list)
    neutral_factors: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    score_calculation: str = ""
    threshold_info: dict[str, str] = Field(default_factory=lambda: {
        "SAFE": "score >= 70",
        "MODERATE": "score >= 45",
        "AVOID": "score < 45",
    })

    def __getitem__(self, item: str) -> Any:
        """Allow subscript access for backward compatibility with legacy dictionary callers."""
        return getattr(self, item)

    def to_legacy_health_dict(self) -> dict[str, Any]:
        """Convert assessment to exact legacy health score dictionary."""
        out: dict[str, Any] = {
            "health_score": self.health_score,
            "decision": self.decision,
            "reason": self.reason,
        }
        if self.diet_note is not None:
            out["diet_note"] = self.diet_note
        return out

    def to_dict(self) -> dict[str, Any]:
        """Convert full assessment to dictionary."""
        return self.model_dump()


class UniversalScoringEngine:
    """Universal Authoritative Scoring Engine for PRAMAAN (FoodScanner AI).

    Consolidates nutrition scoring, assessability determination, ingredient risk,
    additive risk, diet-specific rules, NutriScore prediction, calorie budget decisions,
    and human-readable scoring explanations into one deterministic, side-effect-free pipeline.
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def _get_first_not_none(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    def _normalize_input(
        self, product: Union[CanonicalProduct, dict[str, Any]]
    ) -> tuple[str, Nutrition100g, Optional[str], Optional[str], Optional[str], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Normalize product representation from CanonicalProduct or dict into standard components."""
        if isinstance(product, CanonicalProduct):
            product_name = product.product_name or "Unknown Product"
            nutrition = product.nutrition
            ingredients = product.ingredients
            additives = product.additives
            nutriscore = product.nutriscore
            return product_name, nutrition, ingredients, additives, nutriscore, None, None

        if not isinstance(product, dict):
            raise TypeError(f"Expected product to be CanonicalProduct or dict, got {type(product).__name__}")

        product_name = str(product.get("product_name") or product.get("name") or "Unknown Product")
        ingredients = product.get("ingredients")
        if ingredients is not None:
            ingredients = str(ingredients)
        additives = product.get("additives")
        if additives is not None:
            additives = str(additives)
        nutriscore = product.get("nutriscore")
        if nutriscore is not None:
            nutriscore = str(nutriscore).strip()

        # Extract nutrition: may be flat or nested under 'nutrition'
        nutr_raw = product.get("nutrition")
        if isinstance(nutr_raw, Nutrition100g):
            nutrition = nutr_raw
        elif isinstance(nutr_raw, dict):
            nutrition = Nutrition100g(
                calories=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("calories", "energy-kcal_100g", "energy_kcal", "energy-kcal"))),
                fat=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("fat", "fat_100g", "total_fat"))),
                saturated_fat=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("saturated_fat", "saturated-fat_100g", "saturated_fat_100g"))),
                trans_fat=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("trans_fat", "trans-fat_100g", "trans_fat_100g"))),
                carbs=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("carbs", "carbohydrates", "carbohydrates_100g"))),
                sugar=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("sugar", "sugars", "sugars_100g"))),
                protein=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("protein", "proteins", "proteins_100g"))),
                fiber=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("fiber", "fiber_100g"))),
                salt=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("salt", "salt_100g"))),
                sodium=safe_nutrition_float(self._get_first_not_none(nutr_raw, ("sodium", "sodium_100g"))),
            )
        else:
            # Flat dictionary mapping
            raw_sodium = self._get_first_not_none(product, ("sodium", "sodium_100g"))
            if raw_sodium is None and product.get("sodium_mg") is not None:
                smg = safe_nutrition_float(product.get("sodium_mg"))
                raw_sodium = (smg / 1000.0) if smg is not None else None

            nutrition = Nutrition100g(
                calories=safe_nutrition_float(self._get_first_not_none(product, ("calories", "energy-kcal_100g", "energy_kcal", "energy-kcal"))),
                fat=safe_nutrition_float(self._get_first_not_none(product, ("fat", "fat_100g", "total_fat"))),
                saturated_fat=safe_nutrition_float(self._get_first_not_none(product, ("saturated_fat", "saturated-fat_100g", "saturated_fat_100g"))),
                trans_fat=safe_nutrition_float(self._get_first_not_none(product, ("trans_fat", "trans-fat_100g", "trans_fat_100g"))),
                carbs=safe_nutrition_float(self._get_first_not_none(product, ("carbs", "carbohydrates", "carbohydrates_100g"))),
                sugar=safe_nutrition_float(self._get_first_not_none(product, ("sugar", "sugars", "sugars_100g"))),
                protein=safe_nutrition_float(self._get_first_not_none(product, ("protein", "proteins", "proteins_100g"))),
                fiber=safe_nutrition_float(self._get_first_not_none(product, ("fiber", "fiber_100g"))),
                salt=safe_nutrition_float(self._get_first_not_none(product, ("salt", "salt_100g"))),
                sodium=safe_nutrition_float(raw_sodium),
            )

        ing_analysis = product.get("ingredient_analysis")
        add_analysis = product.get("additive_analysis")
        return product_name, nutrition, ingredients, additives, nutriscore, ing_analysis, add_analysis

    def _assess_general_mandatory(self, nutrition: Nutrition100g) -> tuple[AssessmentStatus, list[str], Optional[float], bool]:
        """Check the 4 general mandatory dimensions: calories, sugar, fat, salt/sodium."""
        effective_salt = resolve_effective_salt(nutrition)
        salt_derived_from_sodium = (nutrition.salt is None and nutrition.sodium is not None)

        missing: list[str] = []
        if nutrition.calories is None:
            missing.append("calories")
        if nutrition.sugar is None:
            missing.append("sugar")
        if nutrition.fat is None:
            missing.append("fat")
        if effective_salt is None:
            missing.append("salt")

        present_count = 4 - len(missing)
        if present_count == 4:
            status = AssessmentStatus.ASSESSABLE
        elif present_count > 0:
            status = AssessmentStatus.PARTIALLY_ASSESSABLE
        else:
            status = AssessmentStatus.UNASSESSABLE

        return status, missing, effective_salt, salt_derived_from_sodium

    def _evaluate_diet_assessability(
        self,
        nutrition: Nutrition100g,
        ingredients: Optional[str],
        effective_salt: Optional[float],
        diet_type: Optional[str],
    ) -> tuple[bool, Optional[str]]:
        """Verify whether product disclosures satisfy user diet-specific mandatory criteria."""
        if not diet_type:
            return True, None

        dt = diet_type.strip().lower()
        if dt == "diabetic":
            has_sugar = nutrition.sugar is not None
            has_carbs = nutrition.carbs is not None
            if not (has_sugar and has_carbs):
                missing = []
                if not has_sugar:
                    missing.append("sugar")
                if not has_carbs:
                    missing.append("carbs")
                return False, f"Missing {', '.join(missing)} disclosure required for diabetic assessment"
            return True, None

        if dt == "low_sodium":
            if effective_salt is None:
                return False, "Missing sodium/salt disclosure required for low-sodium assessment"
            return True, None

        if dt in ("vegan", "vegetarian"):
            if ingredients is None or len(ingredients.strip()) < 3:
                return False, f"Missing ingredients disclosure required for {dt} compliance verification"
            return True, None

        # Unknown diet: general assessability applies
        return True, None

    def _predict_nutriscore(self, nutrition: Nutrition100g) -> Optional[str]:
        """Attempt to predict NutriScore using trained ML ensemble or deterministic heuristic."""
        try:
            from ml_model.predict_nutriscore import predict_nutriscore_with_fallback
            nutr_dict = {
                "calories": nutrition.calories,
                "fat": nutrition.fat,
                "sugar": nutrition.sugar,
                "salt": resolve_effective_salt(nutrition),
                "protein": nutrition.protein,
                "fiber": nutrition.fiber,
                "carbs": nutrition.carbs,
            }
            return predict_nutriscore_with_fallback(nutr_dict)
        except Exception as exc:
            logger.debug("NutriScore prediction unavailable: %s", exc)
            return None

    def score_product(
        self,
        product: Union[CanonicalProduct, dict[str, Any]],
        diet_type: Optional[str] = None,
        remaining_calories: Optional[float] = None,
        product_calories: Optional[float] = None,
        predict_missing_nutriscore: bool = False,
    ) -> ScoringAssessment:
        """Score a product deterministically across general nutrition, risks, diet, and calorie budget."""
        (
            product_name,
            nutrition,
            ingredients,
            additives,
            nutriscore,
            ing_analysis,
            add_analysis,
        ) = self._normalize_input(product)

        # 1. Evaluate general mandatory nutrition completeness
        status, missing_mandatory, effective_salt, salt_from_sodium = self._assess_general_mandatory(nutrition)

        # 2. Enrich ingredient & additive risk analyses if not pre-computed
        if ing_analysis is None:
            ing_analysis = analyze_ingredients(ingredients)
        if add_analysis is None:
            add_analysis = analyze_additives(additives)

        # 3. Handle NutriScore
        predicted_ns: Optional[str] = None
        is_ns_predicted = False
        if nutriscore is None and predict_missing_nutriscore:
            predicted_ns = self._predict_nutriscore(nutrition)
            is_ns_predicted = predicted_ns is not None

        # Effective nutriscore used for bonus/deduction (PRAMAAN health score uses declared nutriscore,
        # or explicit nutriscore field; predicted NutriScore does not overwrite authoritative health score)
        active_nutriscore = nutriscore

        # 4. Calculate individual factors & explanations
        steps: list[dict[str, Any]] = []

        # Sugar
        sug_pen = _sugar_penalty(nutrition.sugar)
        sug_val_str = f"{nutrition.sugar}g" if nutrition.sugar is not None else "N/A"
        steps.append({
            "factor": "Sugar",
            "value": sug_val_str,
            "impact": sug_pen,
            "reason": f"Sugar {sug_val_str} → {sug_pen:+d} points",
        })

        # Salt
        salt_pen = _salt_penalty(effective_salt)
        if effective_salt is not None:
            salt_val_str = f"{effective_salt}g (converted from sodium {nutrition.sodium}g)" if salt_from_sodium else f"{effective_salt}g"
        else:
            salt_val_str = "N/A"
        steps.append({
            "factor": "Salt",
            "value": salt_val_str,
            "impact": salt_pen,
            "reason": f"Salt {salt_val_str} → {salt_pen:+d} points",
        })

        # Fat
        fat_pen = _fat_penalty(nutrition.fat)
        fat_val_str = f"{nutrition.fat}g" if nutrition.fat is not None else "N/A"
        steps.append({
            "factor": "Fat",
            "value": fat_val_str,
            "impact": fat_pen,
            "reason": f"Fat {fat_val_str} → {fat_pen:+d} points",
        })

        # Saturated fat
        sat_fat_pen = _sat_fat_penalty(nutrition.saturated_fat)
        sat_val_str = f"{nutrition.saturated_fat}g" if nutrition.saturated_fat is not None else "N/A"
        steps.append({
            "factor": "Saturated fat",
            "value": sat_val_str,
            "impact": sat_fat_pen,
            "reason": f"Saturated fat {sat_val_str} → {sat_fat_pen:+d} points",
        })

        # Calories
        cal_pen = _calories_penalty(nutrition.calories)
        cal_val_str = f"{nutrition.calories} kcal" if nutrition.calories is not None else "N/A"
        steps.append({
            "factor": "Calories",
            "value": cal_val_str,
            "impact": cal_pen,
            "reason": f"Calories {cal_val_str} → {cal_pen:+d} points",
        })

        # Protein bonus
        prot_bon = _protein_bonus(nutrition.protein)
        prot_val_str = f"{nutrition.protein}g" if nutrition.protein is not None else "N/A"
        steps.append({
            "factor": "Protein",
            "value": prot_val_str,
            "impact": prot_bon,
            "reason": f"Protein {prot_val_str} → {prot_bon:+d} bonus points",
        })

        # Fiber bonus
        fib_bon = _fiber_bonus(nutrition.fiber)
        fib_val_str = f"{nutrition.fiber}g" if nutrition.fiber is not None else "N/A"
        steps.append({
            "factor": "Fiber",
            "value": fib_val_str,
            "impact": fib_bon,
            "reason": f"Fiber {fib_val_str} → {fib_bon:+d} bonus points",
        })

        # Ingredients risk
        ing_pen = _ingredient_risk_penalty(ing_analysis)
        ing_risk = str(ing_analysis.get("risk_level") or "LOW").upper()
        ing_flags = ing_analysis.get("high_risk_flags") or ing_analysis.get("flags") or []
        ing_flags_str = f": {', '.join(ing_flags)}" if ing_flags else ""
        steps.append({
            "factor": "Ingredients",
            "value": f"{ing_risk} risk",
            "impact": ing_pen,
            "reason": f"Ingredients {ing_risk} risk{ing_flags_str} → {ing_pen:+d} points",
        })

        # Additives risk
        add_pen = _additive_risk_penalty(add_analysis)
        add_risk = str(add_analysis.get("risk_level") or "LOW").upper()
        add_items = add_analysis.get("additives") or []
        add_codes = [a.get("code") if isinstance(a, dict) else str(a) for a in add_items]
        add_codes_str = f": {', '.join(filter(None, add_codes))}" if add_codes else ""
        steps.append({
            "factor": "Additives",
            "value": f"{add_risk} risk",
            "impact": add_pen,
            "reason": f"Additives {add_risk} risk{add_codes_str} → {add_pen:+d} points",
        })

        # NutriScore adjustment
        ns_adj = _nutriscore_bonus(active_nutriscore)
        steps.append({
            "factor": "NutriScore",
            "value": active_nutriscore or "N/A",
            "impact": ns_adj,
            "reason": f"NutriScore {active_nutriscore or 'N/A'} → {ns_adj:+d} points",
        })

        # Base score sum before diet adjustments
        base_known_score = (
            100
            + sug_pen
            + salt_pen
            + fat_pen
            + sat_fat_pen
            + cal_pen
            + prot_bon
            + fib_bon
            + ing_pen
            + add_pen
            + ns_adj
        )

        # 5. Evaluate Diet-Specific Rules & Disclosures
        diet_assessable, diet_missing_reason = self._evaluate_diet_assessability(
            nutrition, ingredients, effective_salt, diet_type
        )

        diet_impact = 0
        diet_notes: list[str] = []
        diabetic_force_avoid = False
        diabetic_no_safe = False
        low_sodium_no_safe = False
        vegan_force_avoid = False
        vegetarian_force_avoid = False

        if diet_type:
            dt = diet_type.strip().lower()
            if not diet_assessable and diet_missing_reason:
                diet_notes.append(diet_missing_reason)

            if dt == "diabetic":
                if nutrition.sugar is not None:
                    diet_impact += _sugar_penalty(nutrition.sugar)  # Double sugar penalty
                    if nutrition.sugar > 15:
                        diabetic_force_avoid = True
                        diet_notes.append("High sugar (>15g/100g): AVOID for diabetic diet")
                    elif nutrition.sugar > 5:
                        diabetic_no_safe = True
                        diet_notes.append("Sugar > 5g: not SAFE for diabetic diet")
                    diet_notes.append("Sugar penalty doubled for diabetic diet")
                if nutrition.carbs is not None and nutrition.carbs > 30:
                    diet_impact -= 15
                    diet_notes.append("High carbs for diabetic diet")
                if _contains_any(ingredients, REFINED_SYRUPS_KEYWORDS):
                    diet_impact -= 15
                    diet_notes.append("Contains rapid-spiking refined syrups/sweeteners")

            elif dt == "vegan":
                if ingredients:
                    if _contains_any(ingredients, NON_VEGAN_KEYWORDS):
                        diet_impact -= 30
                        vegan_force_avoid = True
                        diet_notes.append("Contains non-vegan animal-derived ingredients: AVOID for vegan diet")
                    elif diet_assessable:
                        diet_notes.append("Compatible with vegan diet")

            elif dt == "vegetarian":
                if ingredients:
                    if _contains_any(ingredients, NON_VEG_KEYWORDS):
                        diet_impact -= 30
                        vegetarian_force_avoid = True
                        diet_notes.append("Contains non-vegetarian meat/gelatin: AVOID for vegetarian diet")
                    elif diet_assessable:
                        diet_notes.append("Compatible with vegetarian diet")

            elif dt == "low_sodium":
                if effective_salt is not None:
                    # Tripled penalty: base has 1x, diet adds 2x -> 3x total
                    diet_impact += 2 * _salt_penalty(effective_salt)
                    if effective_salt > 1.5:
                        low_sodium_no_safe = True
                        diet_notes.append("High salt (>1.5g/100g) exceeds low-sodium guidelines")
                    diet_notes.append("Salt penalty tripled for low-sodium diet")

            if diet_type and (diet_impact != 0 or diet_notes):
                steps.append({
                    "factor": "Diet adjustment",
                    "value": diet_type,
                    "impact": diet_impact,
                    "reason": ("; ".join(diet_notes) if diet_notes else f"Diet adjustment for {diet_type}") + f" → {diet_impact:+d} points",
                })

        known_score_with_diet = base_known_score + diet_impact

        # 6. Apply Assessability Policy & Calculate Scores / Ranges
        health_score: Optional[int] = None
        score_range: Optional[ScoreRange] = None
        decision: str
        overall_confidence: DataConfidence
        is_complete: bool
        reasons: list[str] = []

        if status == AssessmentStatus.ASSESSABLE:
            is_complete = True
            clamped_score = max(0, min(100, known_score_with_diet))
            health_score = int(round(clamped_score))
            score_range = None

            # Calculate base decision from score
            if health_score >= 70:
                decision = "SAFE"
            elif health_score >= 45:
                decision = "MODERATE"
            else:
                decision = "AVOID"

            # Apply diet constraints on decision
            if diabetic_force_avoid or vegan_force_avoid or vegetarian_force_avoid:
                decision = "AVOID"
            elif diabetic_no_safe and decision == "SAFE":
                decision = "MODERATE"
            elif low_sodium_no_safe and decision == "SAFE":
                decision = "MODERATE"

            # Confidence: HIGH if ingredients present, else MEDIUM
            ing_completeness = calculate_ingredients_completeness(ingredients, additives)
            overall_confidence = DataConfidence.HIGH if ing_completeness >= 0.7 else DataConfidence.MEDIUM

        elif status == AssessmentStatus.PARTIALLY_ASSESSABLE:
            is_complete = False
            health_score = None
            overall_confidence = DataConfidence.LOW

            # Best-case (max_score): missing mandatory nutrients contribute 0 penalty
            max_score = max(0.0, min(100.0, float(known_score_with_diet)))

            # Worst-case (min_score): undeclared mandatory nutrients receive maximum regulatory penalties
            worst_case_penalties = 0
            for dim in missing_mandatory:
                worst_case_penalties += WORST_CASE_PENALTIES.get(dim, 0)

            min_score = max(0.0, min(100.0, float(known_score_with_diet + worst_case_penalties)))
            # Invariant: min_score cannot exceed max_score
            min_score = min(min_score, max_score)

            score_range = ScoreRange(min_score=min_score, max_score=max_score)

            # Decision: AVOID if max_score < 45 or independent avoidance rules mandate avoidance
            if max_score < 45 or diabetic_force_avoid or vegan_force_avoid or vegetarian_force_avoid:
                decision = "AVOID"
            else:
                decision = "INCOMPLETE_DATA"

            reasons.append(f"Missing mandatory nutritional disclosures: {', '.join(missing_mandatory)}")

        else:  # UNASSESSABLE
            is_complete = False
            health_score = None
            score_range = None
            overall_confidence = DataConfidence.UNVERIFIED

            # If independent diet rules mandate avoidance (e.g., non-vegan on vegan diet), return AVOID
            if diabetic_force_avoid or vegan_force_avoid or vegetarian_force_avoid:
                decision = "AVOID"
            else:
                decision = "INSUFFICIENT_DATA"

            reasons.append("Product packaging does not disclose mandatory nutritional values per 100g.")

        # 7. Collect Detected Risk Factor Reasons
        if nutrition.sugar is not None and nutrition.sugar > 20:
            reasons.append("High sugar")
        if effective_salt is not None and effective_salt > 1.5:
            reasons.append("High salt")
        if nutrition.fat is not None and nutrition.fat > 25:
            reasons.append("High fat")
        if nutrition.saturated_fat is not None and nutrition.saturated_fat > 5:
            reasons.append("High saturated fat")
        if nutrition.calories is not None and nutrition.calories > 400:
            reasons.append("High calories")
        if nutrition.protein is not None and nutrition.protein > 10:
            reasons.append("High protein")
        if nutrition.fiber is not None and nutrition.fiber > 6:
            reasons.append("High fiber")
        if ing_analysis.get("risk_level") in {"MEDIUM", "HIGH"}:
            reasons.append(f"Ingredients risk {ing_analysis.get('risk_level')}")
        if add_analysis.get("risk_level") in {"MEDIUM", "HIGH"}:
            reasons.append(f"Additives risk {add_analysis.get('risk_level')}")
        if active_nutriscore:
            reasons.append(f"NutriScore {active_nutriscore}")

        # Summary string for legacy 'reason' field
        legacy_reason = ", ".join(reasons) if reasons else "No major risk factors detected"

        # 8. Evaluate Calorie Budget Integration (Final Decision)
        final_decision = decision
        over_budget = False
        cal_to_check: Optional[float] = None
        if product_calories is not None:
            cal_to_check = safe_nutrition_float(product_calories)
        elif nutrition.calories is not None:
            cal_to_check = nutrition.calories

        rem_cal = safe_nutrition_float(remaining_calories)
        if rem_cal is not None and cal_to_check is not None:
            over_budget = cal_to_check > rem_cal

        if over_budget:
            if final_decision == "SAFE":
                final_decision = "MODERATE"
            elif final_decision in ("MODERATE", "INCOMPLETE_DATA"):
                final_decision = "AVOID"
            # AVOID stays AVOID; INSUFFICIENT_DATA stays INSUFFICIENT_DATA unless known calories violate

        # 9. Format Calculation and Explanations
        positive_factors = [s for s in steps if s.get("impact", 0) > 0]
        negative_factors = [s for s in steps if s.get("impact", 0) < 0]
        neutral_factors = [s for s in steps if s.get("impact", 0) == 0]

        if status == AssessmentStatus.ASSESSABLE and health_score is not None:
            parts = ["100"]
            for step in steps:
                parts.append(f"{step['impact']:+d}")
            calc_str = " ".join(parts) + f" = {health_score}"
            summary = (
                f"Base score: 100. Evaluated {len(steps)} nutritional and ingredient criteria. "
                f"{len(negative_factors)} deduction(s) and {len(positive_factors)} bonus(es) result in a score of {health_score}/100 ({final_decision})."
            )
        elif status == AssessmentStatus.PARTIALLY_ASSESSABLE and score_range is not None:
            calc_str = (
                f"Partial evaluation: known baseline = {round(max_score, 1)}, "
                f"bounded score range [{round(score_range.min_score, 1)} - {round(score_range.max_score, 1)}] "
                f"due to missing mandatory: {', '.join(missing_mandatory)}"
            )
            summary = (
                f"Partial nutritional disclosure. {len(missing_mandatory)} mandatory dimension(s) undeclared ({', '.join(missing_mandatory)}). "
                f"Estimated score range: {round(score_range.min_score, 1)}–{round(score_range.max_score, 1)}/100 ({final_decision})."
            )
        else:
            calc_str = "Unassessable: mandatory nutritional values not disclosed on packaging."
            summary = (
                f"Unassessable nutritional profile. Mandatory nutrition dimensions (calories, sugar, fat, salt) "
                f"are absent. Health score cannot be calculated ({final_decision})."
            )

        diet_note_str = "; ".join(diet_notes) if diet_notes else None

        return ScoringAssessment(
            product_name=product_name,
            assessment_status=status,
            health_score=health_score,
            score_range=score_range,
            decision=decision,
            final_decision=final_decision,
            reasons=reasons,
            reason=legacy_reason,
            overall_confidence=overall_confidence,
            is_complete=is_complete,
            missing_mandatory_nutrients=missing_mandatory,
            diet_note=diet_note_str,
            is_diet_assessable=diet_assessable,
            nutriscore=active_nutriscore,
            predicted_nutriscore=predicted_ns,
            is_nutriscore_predicted=is_ns_predicted,
            ingredient_analysis=ing_analysis,
            additive_analysis=add_analysis,
            steps=steps,
            positive_factors=positive_factors,
            negative_factors=negative_factors,
            neutral_factors=neutral_factors,
            summary=summary,
            score_calculation=calc_str,
        )

    def evaluate_final_decision(
        self,
        health_score_result: dict[str, Any],
        remaining_calories: float,
        product_calories: float,
    ) -> dict[str, Any]:
        """Wrap or compute final decision considering calorie budget and base decision."""
        health_score = health_score_result.get("health_score")
        if health_score is not None:
            try:
                health_score = float(health_score)
            except (ValueError, TypeError):
                health_score = None

        base_decision = str(health_score_result.get("decision") or health_score_result.get("final_decision") or "").upper()
        rem_cal = safe_nutrition_float(remaining_calories)
        prod_cal = safe_nutrition_float(product_calories)

        over_budget = False
        if rem_cal is not None and prod_cal is not None:
            over_budget = prod_cal > rem_cal

        final_decision = base_decision
        if base_decision == "SAFE" and over_budget:
            final_decision = "MODERATE"
        elif base_decision == "MODERATE" and over_budget:
            final_decision = "AVOID"
        elif base_decision == "INCOMPLETE_DATA" and over_budget:
            final_decision = "AVOID"
        elif base_decision == "AVOID":
            final_decision = "AVOID"
        elif base_decision == "INSUFFICIENT_DATA":
            final_decision = "INSUFFICIENT_DATA"

        if over_budget:
            reason = f"Health score suggests {base_decision}, but exceeds remaining calorie budget"
        else:
            reason = f"Health score suggests {base_decision}"

        out = {
            "final_decision": final_decision,
            "health_score": health_score,
            "reason": reason,
        }
        # Propagate auxiliary fields if present in input
        for key in (
            "assessment_status",
            "score_range",
            "overall_confidence",
            "is_complete",
            "missing_mandatory_nutrients",
            "diet_note",
        ):
            if key in health_score_result:
                out[key] = health_score_result[key]

        return out

    def explain_product_score(
        self, product: dict[str, Any], diet_type: Optional[str] = None
    ) -> dict[str, Any]:
        """Provide detailed human-readable breakdown of product evaluation."""
        assessment = self.score_product(product, diet_type=diet_type)
        return {
            "product_name": assessment.product_name,
            "final_score": assessment.health_score,
            "final_decision": assessment.decision,
            "base_score": 100,
            "steps": assessment.steps,
            "positive_factors": assessment.positive_factors,
            "negative_factors": assessment.negative_factors,
            "neutral_factors": assessment.neutral_factors,
            "summary": assessment.summary,
            "score_calculation": assessment.score_calculation,
            "diet_note": assessment.diet_note,
            "threshold_info": assessment.threshold_info,
            "assessment_status": assessment.assessment_status.value,
            "overall_confidence": assessment.overall_confidence.value,
            "is_complete": assessment.is_complete,
            "missing_mandatory_nutrients": assessment.missing_mandatory_nutrients,
            "score_range": assessment.score_range.model_dump() if assessment.score_range else None,
        }


@lru_cache(maxsize=1)
def get_default_scoring_engine() -> UniversalScoringEngine:
    """Return the cached singleton UniversalScoringEngine."""
    return UniversalScoringEngine()
