"""Phase 5 Comprehensive Universal Scoring Engine Test Suite.

Covers:
1. Nutrition correctness (complete, explicit zeros, salt/sodium exact 2.5 conversion, invalid/non-finite).
2. Assessability states (ASSESSABLE, PARTIALLY_ASSESSABLE, UNASSESSABLE, no zero fabrication, no fake 50 score, min <= max range).
3. Diet profiles (diabetic, low-sodium, vegan, vegetarian, missing diet disclosures, no false vegan).
4. Other scoring logic (ingredient risk, additive risk, NutriScore prediction distinctness, calorie budget decisions).
5. Legacy compatibility (food_health_score, final_decision_engine, score_explainer).
6. Determinism, immutability, side-effect freedom.
7. Benchmark products regression (Maggi, Parle-G, Amul Butter, Kurkure, Coca-Cola).
"""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest

from schemas.product import (
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    Nutrition100g,
    ScoreRange,
)
from services.final_decision_engine import compute_final_decision
from services.food_health_score import (
    _additive_risk_penalty,
    _calories_penalty,
    _fat_penalty,
    _fiber_bonus,
    _ingredient_risk_penalty,
    _nutriscore_bonus,
    _protein_bonus,
    _salt_penalty,
    _sat_fat_penalty,
    _sugar_penalty,
    _to_float,
    compute_diet_aware_score,
    compute_food_health_score,
)
from services.score_explainer import explain_score
from services.scoring_engine import (
    UniversalScoringEngine,
    get_default_scoring_engine,
    safe_nutrition_float,
)


@pytest.fixture
def engine() -> UniversalScoringEngine:
    return get_default_scoring_engine()


# ==============================================================================
# 1. NUTRITION CORRECTNESS & PARSING
# ==============================================================================

class TestNutritionCorrectness:
    """Tests for normalized 100g nutrition calculations, zero handling, and conversions."""

    def test_complete_nutrition_standard_penalties(self, engine: UniversalScoringEngine):
        """Standard complete profile with known values must compute deterministic score."""
        prod = {
            "calories": 250.0,  # <= 400: -5
            "sugar": 8.0,       # <= 10: -10
            "fat": 5.0,         # <= 17.5: -10
            "saturated_fat": 2.0,  # <= 5: 0
            "salt": 0.5,        # <= 1.0: -10
            "protein": 7.0,     # > 5: +8
            "fiber": 4.0,       # > 3: +8
        }
        # 100 - 5 - 10 - 10 - 0 - 10 + 8 + 8 = 81
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert res.health_score == 81
        assert res.decision == "SAFE"
        assert res.is_complete is True
        assert res.score_range is None

    def test_explicit_zeros_are_preserved_not_treated_as_missing(self, engine: UniversalScoringEngine):
        """Explicitly declared 0.0 values must remain 0.0, receiving 0 penalty and ASSESSABLE status."""
        prod = {
            "calories": 150.0,  # < 200: 0
            "sugar": 0.0,       # <= 5: 0
            "fat": 0.0,         # <= 3: 0
            "salt": 0.0,        # <= 0.3: 0
        }
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert res.health_score == 100
        assert res.decision == "SAFE"
        assert res.is_complete is True
        assert len(res.missing_mandatory_nutrients) == 0

    def test_salt_conversion_from_sodium_exact(self, engine: UniversalScoringEngine):
        """When salt is absent, sodium must be converted exactly using factor 2.5."""
        # sodium 0.4g -> salt = 0.4 * 2.5 = 1.0g -> _salt_penalty(1.0) is -10
        prod = {
            "calories": 100.0,
            "sugar": 2.0,
            "fat": 2.0,
            "sodium": 0.4,
        }
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        # Base: 100, calories: 0, sugar: 0, fat: 0, salt: -10 -> 90
        assert res.health_score == 90
        assert any("converted from sodium 0.4g" in s["reason"] for s in res.steps if s["factor"] == "Salt")

    def test_salt_takes_precedence_over_sodium(self, engine: UniversalScoringEngine):
        """When both package salt and sodium are present, salt takes precedence for scoring."""
        prod = {
            "calories": 100.0,
            "sugar": 2.0,
            "fat": 2.0,
            "salt": 1.8,       # > 1.5: -30 penalty
            "sodium": 0.1,     # Converted would be 0.25 (0 penalty)
        }
        res = engine.score_product(prod)
        # Salt 1.8g takes precedence -> -30 penalty -> 70
        assert res.health_score == 70
        assert "High salt" in res.reasons

    def test_unit_parsing_and_conversion(self, engine: UniversalScoringEngine):
        """String values with 'kcal', 'g', and 'mg' should be parsed correctly."""
        prod = {
            "calories": "350 kcal",
            "sugar": "12g",
            "fat": "4g",
            "sodium": "800mg",  # 0.8g sodium -> 2.0g salt -> > 1.5: -30
        }
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        # calories: -5, sugar: -20, fat: -10, salt: -30 -> 100 - 65 = 35 -> AVOID
        assert res.health_score == 35
        assert res.decision == "AVOID"

    def test_invalid_and_non_finite_values_safely_handled(self, engine: UniversalScoringEngine):
        """Negative, NaN, inf, and unparseable values must be safely rejected without crashing."""
        assert safe_nutrition_float(-15.0) is None
        assert safe_nutrition_float(float("nan")) is None
        assert safe_nutrition_float(float("inf")) is None
        assert safe_nutrition_float("not_a_number") is None
        assert safe_nutrition_float("") is None
        assert safe_nutrition_float(None) is None
        assert safe_nutrition_float(0.0) == 0.0
        assert safe_nutrition_float("15.5") == 15.5


# ==============================================================================
# 2. ASSESSABILITY STATES & MISSING NUTRITION POLICY
# ==============================================================================

class TestAssessabilityPolicy:
    """Tests enforcing the strict ASSESSABLE, PARTIALLY_ASSESSABLE, and UNASSESSABLE contract."""

    def test_all_four_mandatory_present_is_assessable(self, engine: UniversalScoringEngine):
        prod = {"calories": 200, "sugar": 5, "fat": 2, "salt": 0.2}
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert res.health_score is not None
        assert res.score_range is None
        assert res.is_complete is True

    def test_one_mandatory_missing_is_partially_assessable(self, engine: UniversalScoringEngine):
        """Missing calories (1 mandatory missing) -> PARTIALLY_ASSESSABLE, null health_score, bounded range."""
        prod = {
            "sugar": 4.0,  # 0 penalty
            "fat": 2.0,    # 0 penalty
            "salt": 0.2,   # 0 penalty
            # calories is missing!
        }
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert res.health_score is None  # NEVER fabricated!
        assert res.is_complete is False
        assert res.overall_confidence == DataConfidence.LOW
        assert "calories" in res.missing_mandatory_nutrients
        assert res.score_range is not None

        # Max score: known baseline = 100
        # Min score: known (100) - worst-case calories penalty (-15) = 85
        assert res.score_range.max_score == 100.0
        assert res.score_range.min_score == 85.0
        assert res.score_range.min_score <= res.score_range.max_score
        assert res.decision == "INCOMPLETE_DATA"  # Never SAFE merely from max_score!

    def test_multiple_mandatory_missing_is_partially_assessable(self, engine: UniversalScoringEngine):
        """Missing sugar and salt (2 mandatory missing) -> bounded range with -35 and -30 penalties."""
        prod = {
            "calories": 150.0,  # 0 penalty
            "fat": 15.0,        # -10 penalty
            # sugar missing (-35 worst-case)
            # salt missing (-30 worst-case)
        }
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert res.health_score is None
        assert set(res.missing_mandatory_nutrients) == {"sugar", "salt"}
        # Max score: 100 - 10 = 90
        # Min score: 90 - 35 (sugar) - 30 (salt) = 25
        assert res.score_range.max_score == 90.0
        assert res.score_range.min_score == 25.0
        assert res.score_range.min_score <= res.score_range.max_score
        assert res.decision == "INCOMPLETE_DATA"

    def test_partial_assessable_with_max_score_below_45_mandates_avoid(self, engine: UniversalScoringEngine):
        """When known deductions make max_score < 45, decision must be AVOID even if incomplete."""
        prod = {
            "calories": 550.0,   # -15
            "fat": 35.0,         # -30
            "saturated_fat": 15, # -10
            "ingredient_analysis": {"risk_level": "HIGH"},  # -25
            # sugar and salt are missing!
        }
        # Known score: 100 - 15 - 30 - 10 - 25 = 20
        # max_score = 20.0 < 45
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert res.health_score is None
        assert res.score_range.max_score == 20.0
        assert res.score_range.min_score == 0.0
        assert res.decision == "AVOID"

    def test_all_four_mandatory_missing_is_unassessable(self, engine: UniversalScoringEngine):
        """When all 4 mandatory dimensions are missing -> UNASSESSABLE, null health_score, INSUFFICIENT_DATA."""
        prod = {"product_name": "Zero Disclosure Item"}
        res = engine.score_product(prod)
        assert res.assessment_status == AssessmentStatus.UNASSESSABLE
        assert res.health_score is None
        assert res.score_range is None
        assert res.decision == "INSUFFICIENT_DATA"
        assert res.overall_confidence == DataConfidence.UNVERIFIED
        assert res.is_complete is False
        assert "Product packaging does not disclose mandatory nutritional values per 100g." in res.reason

    def test_never_returns_fabricated_50_or_100_for_empty_product(self, engine: UniversalScoringEngine):
        """Verify absolute rejection of fabricated compatibility scores for empty disclosures."""
        res = engine.score_product({})
        assert res.health_score is None
        assert res.decision != "SAFE"
        assert res.decision != "MODERATE"
        assert res.decision == "INSUFFICIENT_DATA"


# ==============================================================================
# 3. DIET-SPECIFIC MANDATORY REQUIREMENTS & RULES
# ==============================================================================

class TestDietProfiles:
    """Tests for diet-specific assessability checks and rule evaluations."""

    def test_diabetic_assessability_missing_carbs(self, engine: UniversalScoringEngine):
        """Diabetic diet requires both sugar AND carbs; missing carbs makes diet unassessable."""
        prod = {
            "calories": 200,
            "fat": 5,
            "salt": 0.5,
            "sugar": 5.0,
            # carbs missing!
        }
        res = engine.score_product(prod, diet_type="diabetic")
        assert res.is_diet_assessable is False
        assert "Missing carbs disclosure required for diabetic assessment" in res.diet_note

    def test_diabetic_high_sugar_forces_avoid(self, engine: UniversalScoringEngine):
        """Diabetic profile with sugar > 15g forces AVOID and applies double penalty."""
        prod = {
            "calories": 300,
            "fat": 2,
            "salt": 0.5,
            "sugar": 18.0,  # > 15g -> force AVOID
            "carbs": 40.0,  # > 30g -> -15 penalty
        }
        res = engine.score_product(prod, diet_type="diabetic")
        assert res.decision == "AVOID"
        assert "High sugar (>15g/100g): AVOID for diabetic diet" in res.diet_note
        assert "High carbs for diabetic diet" in res.diet_note

    def test_diabetic_moderate_sugar_prevents_safe(self, engine: UniversalScoringEngine):
        """Diabetic profile with sugar > 5g cannot be SAFE (capped at MODERATE)."""
        prod = {
            "calories": 100,
            "fat": 1,
            "salt": 0.1,
            "sugar": 6.0,  # > 5g: not SAFE
            "carbs": 15.0, # <= 30: 0 penalty
        }
        res = engine.score_product(prod, diet_type="diabetic")
        # Base score was 100 - 10 (sugar) = 90 (SAFE), but diabetic caps at MODERATE
        assert res.decision == "MODERATE"
        assert "Sugar > 5g: not SAFE for diabetic diet" in res.diet_note

    def test_low_sodium_diet_triples_salt_penalty(self, engine: UniversalScoringEngine):
        """Low-sodium diet triples salt penalty (1x base + 2x diet = 3x total)."""
        prod = {
            "calories": 100,
            "sugar": 0,
            "fat": 0,
            "salt": 1.2,  # _salt_penalty is -20
        }
        # Base score: 100 - 20 = 80
        # Low-sodium adds 2 * (-20) = -40 -> 80 - 40 = 40 -> AVOID
        res = engine.score_product(prod, diet_type="low_sodium")
        assert res.health_score == 40
        assert res.decision == "AVOID"
        assert "Salt penalty tripled for low-sodium diet" in res.diet_note

    def test_low_sodium_missing_salt_and_sodium_is_unassessable(self, engine: UniversalScoringEngine):
        prod = {"calories": 100, "sugar": 0, "fat": 0}
        res = engine.score_product(prod, diet_type="low_sodium")
        assert res.is_diet_assessable is False
        assert "Missing sodium/salt disclosure required for low-sodium assessment" in res.diet_note

    def test_vegan_diet_forces_avoid_on_animal_ingredients(self, engine: UniversalScoringEngine):
        """Vegan profile with milk/butter/whey forces AVOID and deducts 30 points."""
        prod = {
            "calories": 250,
            "sugar": 4,
            "fat": 5,
            "salt": 0.2,
            "ingredients": "Rolled oats, almond butter, whey protein, honey",
        }
        res = engine.score_product(prod, diet_type="vegan")
        assert res.decision == "AVOID"
        assert "Contains non-vegan animal-derived ingredients: AVOID for vegan diet" in res.diet_note

    def test_vegan_diet_compatible_when_plant_based(self, engine: UniversalScoringEngine):
        prod = {
            "calories": 150,
            "sugar": 2,
            "fat": 2,
            "salt": 0.2,
            "ingredients": "Organic oats, chia seeds, cocoa powder, stevia",
        }
        res = engine.score_product(prod, diet_type="vegan")
        assert res.decision == "SAFE"
        assert "Compatible with vegan diet" in res.diet_note

    def test_vegetarian_diet_forces_avoid_on_meat_and_gelatin(self, engine: UniversalScoringEngine):
        prod = {
            "calories": 300,
            "sugar": 5,
            "fat": 10,
            "salt": 0.8,
            "ingredients": "Pork fat, chicken broth, gelatin, wheat flour",
        }
        res = engine.score_product(prod, diet_type="vegetarian")
        assert res.decision == "AVOID"
        assert "Contains non-vegetarian meat/gelatin: AVOID for vegetarian diet" in res.diet_note

    def test_no_false_vegan_or_vegetarian_when_ingredients_absent(self, engine: UniversalScoringEngine):
        """If ingredients are missing, engine must NOT classify product as vegan/vegetarian compatible."""
        prod = {
            "calories": 200,
            "sugar": 5,
            "fat": 5,
            "salt": 0.5,
            "ingredients": None,
        }
        res_vegan = engine.score_product(prod, diet_type="vegan")
        res_veg = engine.score_product(prod, diet_type="vegetarian")
        assert res_vegan.is_diet_assessable is False
        assert res_veg.is_diet_assessable is False
        assert "Missing ingredients disclosure required for vegan compliance verification" in res_vegan.diet_note
        assert "Missing ingredients disclosure required for vegetarian compliance verification" in res_veg.diet_note


# ==============================================================================
# 4. INGREDIENT RISK, ADDITIVES, NUTRISCORE & CALORIE BUDGET
# ==============================================================================

class TestRisksAndCalorieBudget:
    """Tests for ingredients/additive penalties, NutriScore prediction distinctness, and calorie budget."""

    def test_high_risk_ingredients_penalty(self, engine: UniversalScoringEngine):
        """Partially hydrogenated oil triggers HIGH ingredient risk (-25 penalty)."""
        prod = {
            "calories": 200,  # <= 400: -5
            "sugar": 2,       # <= 5: 0
            "fat": 5,         # <= 17.5: -10
            "salt": 0.2,      # <= 0.3: 0
            "ingredients": "Wheat flour, partially hydrogenated oil, salt",
        }
        res = engine.score_product(prod)
        assert res.ingredient_analysis["risk_level"] == "HIGH"
        # Base: 100 - 5 (cals) - 10 (fat) - 25 (ingr) = 60 -> MODERATE
        assert res.health_score == 60
        assert res.decision == "MODERATE"

    def test_high_risk_additives_penalty(self, engine: UniversalScoringEngine):
        """Additives like e102 / e211 trigger HIGH additive risk (-25 penalty)."""
        prod = {
            "calories": 150,
            "sugar": 2,
            "fat": 2,
            "salt": 0.2,
            "additives": "en:e102, en:e211",
        }
        res = engine.score_product(prod)
        assert res.additive_analysis["risk_level"] == "HIGH"
        # Base: 100 - 25 = 75
        assert res.health_score == 75

    def test_nutriscore_adjustments_and_distinct_output(self, engine: UniversalScoringEngine):
        """NutriScore bonus is applied to health_score, but prediction does NOT overwrite PRAMAAN score."""
        prod_a = {"calories": 100, "sugar": 0, "fat": 0, "salt": 0, "nutriscore": "A"}
        prod_e = {"calories": 100, "sugar": 0, "fat": 0, "salt": 0, "nutriscore": "E"}
        res_a = engine.score_product(prod_a)
        res_e = engine.score_product(prod_e)
        # 100 + 15 = 115 clamped to 100
        assert res_a.health_score == 100
        # 100 - 20 = 80
        assert res_e.health_score == 80

    def test_calorie_budget_demotions(self, engine: UniversalScoringEngine):
        """Exceeding calorie budget demotes SAFE -> MODERATE, and MODERATE -> AVOID."""
        prod_safe = {"calories": 450, "sugar": 2, "fat": 2, "salt": 0.2}  # Score = 85 (SAFE)
        # Exceeds remaining 300 kcal
        res_safe_over = engine.score_product(prod_safe, remaining_calories=300)
        assert res_safe_over.decision == "SAFE"          # Base health decision
        assert res_safe_over.final_decision == "MODERATE" # Demoted by calorie budget

        prod_mod = {"calories": 450, "sugar": 15, "fat": 15, "salt": 1.2} # Score ~ 50 (MODERATE)
        res_mod_over = engine.score_product(prod_mod, remaining_calories=300)
        assert res_mod_over.final_decision == "AVOID"


# ==============================================================================
# 5. LEGACY COMPATIBILITY & HELPER EXPORTS
# ==============================================================================

class TestLegacyCompatibility:
    """Verify legacy functions delegate to UniversalScoringEngine without breaking callers."""

    def test_legacy_compute_food_health_score(self):
        prod = {"calories": 150, "sugar": 2, "fat": 2, "salt": 0.2}
        legacy_res = compute_food_health_score(prod)
        assert set(legacy_res.keys()) == {"health_score", "decision", "reason"}
        assert legacy_res["health_score"] == 100
        assert legacy_res["decision"] == "SAFE"

    def test_legacy_compute_diet_aware_score(self):
        prod = {"calories": 200, "sugar": 2, "fat": 2, "salt": 0.2, "ingredients": "Oats, water"}
        legacy_diet = compute_diet_aware_score(prod, diet_type="vegan")
        assert "health_score" in legacy_diet
        assert "decision" in legacy_diet
        assert "diet_note" in legacy_diet

    def test_legacy_compute_final_decision(self):
        health_res = {"health_score": 85, "decision": "SAFE"}
        final_res = compute_final_decision(health_res, remaining_calories=200, product_calories=350)
        assert final_res["final_decision"] == "MODERATE"
        assert final_res["health_score"] == 85.0

    def test_legacy_explain_score(self):
        prod = {
            "product_name": "Test Snack",
            "calories": 300,
            "sugar": 12,
            "fat": 10,
            "saturated_fat": 6,
            "salt": 0.8,
            "protein": 6,
            "fiber": 4,
        }
        exp = explain_score(prod, None)
        assert exp["product_name"] == "Test Snack"
        assert "steps" in exp
        assert "final_score" in exp
        assert "summary" in exp
        assert "score_calculation" in exp
        # Saturated fat step check
        sat_step = next(s for s in exp["steps"] if s["factor"] == "Saturated fat")
        assert sat_step["impact"] == -10


# ==============================================================================
# 6. DETERMINISM & IMMUTABILITY
# ==============================================================================

class TestDeterminismAndImmutability:
    """Verify engine is side-effect-free, deterministic, and does not mutate inputs."""

    def test_repeated_scoring_is_strictly_deterministic(self, engine: UniversalScoringEngine):
        prod = {
            "calories": 420.0,
            "sugar": 18.0,
            "fat": 16.0,
            "salt": 1.4,
            "protein": 7.0,
            "fiber": 3.2,
        }
        res1 = engine.score_product(prod)
        res2 = engine.score_product(prod)
        assert res1.health_score == res2.health_score
        assert res1.decision == res2.decision
        assert res1.steps == res2.steps

    def test_input_product_is_not_mutated(self, engine: UniversalScoringEngine):
        prod = {
            "product_name": "Immutable Bar",
            "calories": 250,
            "sugar": 10,
            "fat": 5,
            "salt": 0.5,
            "sodium": 0.2,
        }
        prod_copy = copy.deepcopy(prod)
        engine.score_product(prod, diet_type="vegan", remaining_calories=500)
        assert prod == prod_copy

    def test_canonical_product_input_is_not_mutated(self, engine: UniversalScoringEngine):
        canon = CanonicalProduct(
            canonical_id="CANON-IMMUTABLE-01",
            barcode="8901234567890",
            product_name="Immutable Canonical Product",
            nutrition=Nutrition100g(calories=300.0, sugar=8.0, fat=6.0, salt=0.8),
        )
        canon_copy = canon.model_copy(deep=True)
        res = engine.score_product(canon)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert canon == canon_copy


# ==============================================================================
# 7. BENCHMARK PRODUCTS REGRESSION SUITE
# ==============================================================================

class TestBenchmarkProductsRegression:
    """Benchmark tests across Maggi, Parle-G, Amul Butter, Kurkure, and Coca-Cola."""

    def test_benchmark_maggi_complete_variant(self, engine: UniversalScoringEngine):
        """Maggi 2-Minute Masala Noodles with complete nutrition scores 31 (AVOID)."""
        maggi = {
            "product_name": "Maggi 2-Minute Masala Noodles",
            "calories": 427.0,
            "fat": 15.7,
            "saturated_fat": 6.8,
            "carbs": 63.5,
            "sugar": 2.2,
            "protein": 8.0,
            "fiber": 3.5,
            "salt": 2.3,
            "ingredients": "Wheat flour, Palm oil, Salt, Spices",
            "nutriscore": "d",
        }
        res = engine.score_product(maggi)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert res.health_score == 31
        assert res.decision == "AVOID"
        assert "High salt" in res.reasons
        assert "High saturated fat" in res.reasons
        assert "High calories" in res.reasons

    def test_benchmark_maggi_incomplete_db_shell_variant(self, engine: UniversalScoringEngine):
        """Maggi shell entry with barcode only and no nutrition must be UNASSESSABLE."""
        maggi_shell = CanonicalProduct(
            canonical_id="UPC-8901058851304",
            barcode="8901058851304",
            product_name="Maggi 2-Minute Noodles Incomplete Shell",
            nutrition=Nutrition100g(),
        )
        res = engine.score_product(maggi_shell)
        assert res.assessment_status == AssessmentStatus.UNASSESSABLE
        assert res.health_score is None
        assert res.decision == "INSUFFICIENT_DATA"
        assert res.overall_confidence == DataConfidence.UNVERIFIED

    def test_benchmark_parle_g_complete_and_diabetic(self, engine: UniversalScoringEngine):
        """Parle-G biscuit complete profile scores 0 (AVOID); diabetic profile mandates AVOID."""
        parle_g = {
            "product_name": "Parle-G Gluco Biscuits",
            "calories": 454.0,
            "fat": 12.5,
            "saturated_fat": 6.5,
            "carbs": 78.0,
            "sugar": 26.0,
            "protein": 6.5,
            "fiber": 1.5,
            "salt": 0.7,
            "ingredients": "Refined wheat flour, Sugar, Refined palm oil, Invert sugar syrup",
            "nutriscore": "e",
        }
        res_general = engine.score_product(parle_g)
        assert res_general.health_score == 0
        assert res_general.decision == "AVOID"

        res_diabetic = engine.score_product(parle_g, diet_type="diabetic")
        assert res_diabetic.health_score == 0
        assert res_diabetic.decision == "AVOID"
        assert "High sugar (>15g/100g): AVOID for diabetic diet" in res_diabetic.diet_note

    def test_benchmark_amul_butter_complete_and_vegan(self, engine: UniversalScoringEngine):
        """Amul Butter is high fat/salt (score 0, AVOID); vegan diet detects dairy butter (AVOID)."""
        butter = {
            "product_name": "Amul Pasteurised Butter",
            "calories": 720.0,
            "fat": 80.0,
            "saturated_fat": 51.0,
            "carbs": 0.0,
            "sugar": 0.0,
            "protein": 0.5,
            "fiber": 0.0,
            "salt": 2.5,
            "ingredients": "Butter, Common Salt",
            "nutriscore": "e",
        }
        res = engine.score_product(butter, diet_type="vegan")
        assert res.health_score == 0
        assert res.decision == "AVOID"
        assert "Contains non-vegan animal-derived ingredients: AVOID for vegan diet" in res.diet_note

    def test_benchmark_kurkure_masala_munch(self, engine: UniversalScoringEngine):
        """Kurkure complete profile has high salt, fat, calories (score 3, AVOID)."""
        kurkure = {
            "product_name": "Kurkure Masala Munch",
            "calories": 560.0,
            "fat": 35.0,
            "saturated_fat": 15.0,
            "carbs": 55.0,
            "sugar": 2.0,
            "protein": 6.0,
            "fiber": 2.0,
            "salt": 2.2,
            "ingredients": "Rice meal, Corn meal, Gram meal, Palmolein oil, Seasoning",
            "nutriscore": "e",
        }
        res = engine.score_product(kurkure)
        assert res.assessment_status == AssessmentStatus.ASSESSABLE
        assert res.health_score == 3
        assert res.decision == "AVOID"

    def test_benchmark_coca_cola_variants(self, engine: UniversalScoringEngine):
        """Coca-Cola complete profile scores 50 (MODERATE); diabetic profile mandates AVOID."""
        coke = {
            "product_name": "Coca-Cola Classic",
            "calories": 42.0,
            "fat": 0.0,
            "saturated_fat": 0.0,
            "carbs": 10.6,
            "sugar": 10.6,
            "protein": 0.0,
            "fiber": 0.0,
            "salt": 0.0,
            "additives": "en:e150d",
            "nutriscore": "e",
        }
        res_general = engine.score_product(coke)
        assert res_general.health_score == 50
        assert res_general.decision == "MODERATE"

        res_diabetic = engine.score_product(coke, diet_type="diabetic")
        # Sugar > 10g + diabetic double penalty: 50 - 20 = 30 -> AVOID
        assert res_diabetic.health_score == 30
        assert res_diabetic.decision == "AVOID"

        # Partial Coca-Cola profile (calories and sugar only, missing fat & salt)
        coke_partial = {
            "product_name": "Coca-Cola Partial",
            "calories": 42.0,
            "sugar": 10.6,
        }
        res_partial = engine.score_product(coke_partial)
        assert res_partial.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert res_partial.health_score is None
        assert res_partial.score_range is not None
        assert res_partial.decision == "INCOMPLETE_DATA"
