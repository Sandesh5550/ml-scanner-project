from __future__ import annotations

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from schemas.product import (
    TOTAL_NUTRITION_SLOTS,
    AssessmentStatus,
    CanonicalProduct,
    DataConfidence,
    DataProvenance,
    FieldProvenance,
    Nutrition100g,
    ScoreRange,
    calculate_ingredients_completeness,
    calculate_nutrition_completeness,
    calculate_salt_from_sodium,
    derive_product_assessment,
    is_assessable_for_diet,
    resolve_effective_salt,
)


class TestCanonicalSchemasBasics:
    """Tests for schema structure, serialization, and non-negative constraints."""

    def test_valid_canonical_product_creation(self):
        prod = CanonicalProduct(
            canonical_id="UPC-8901058851304",
            barcode="8901058851304",
            product_name="Maggi 2-Minute Masala Noodles",
            brand="Nestle",
            category="Instant Noodles",
            nutrition=Nutrition100g(
                calories=427.0,
                fat=15.7,
                saturated_fat=6.8,
                carbs=63.5,
                sugar=2.2,
                protein=8.0,
                fiber=3.5,
                salt=2.3,
            ),
            ingredients="Wheat flour, Palm oil, Salt, Spices",
            nutriscore="d",
        )

        assert prod.canonical_id == "UPC-8901058851304"
        assert prod.barcode == "8901058851304"
        assert prod.product_name == "Maggi 2-Minute Masala Noodles"
        assert prod.brand == "Nestle"
        assert prod.nutrition.calories == 427.0
        assert prod.nutrition.sugar == 2.2
        assert prod.assessment_status == AssessmentStatus.ASSESSABLE
        assert prod.overall_confidence == DataConfidence.HIGH
        assert prod.nutrition_completeness == 1.0
        assert prod.ingredients_completeness == 0.7

    def test_json_serialization_roundtrip(self):
        prod = CanonicalProduct(
            canonical_id="TEST-001",
            barcode="12345678",
            product_name="Test Cookie",
            nutrition=Nutrition100g(calories=100.0, fat=5.0, sugar=2.0, salt=0.1),
            ingredients="Flour, Sugar",
        )
        json_str = prod.model_dump_json()
        restored = CanonicalProduct.model_validate_json(json_str)

        assert restored.canonical_id == prod.canonical_id
        assert restored.barcode == prod.barcode
        assert restored.nutrition.calories == prod.nutrition.calories
        assert restored.nutrition.sugar == prod.nutrition.sugar
        assert restored.assessment_status == prod.assessment_status
        assert restored.overall_confidence == prod.overall_confidence
        assert restored.nutrition_completeness == prod.nutrition_completeness
        assert restored.ingredients_completeness == prod.ingredients_completeness


class TestMissingNutritionAndZeroImputationInvariant:
    """Verifies missing nutrition is strictly nullable and never converted to 0.0."""

    def test_missing_nutrition_fields_remain_none(self):
        nutr = Nutrition100g()
        assert nutr.calories is None
        assert nutr.fat is None
        assert nutr.saturated_fat is None
        assert nutr.trans_fat is None
        assert nutr.carbs is None
        assert nutr.sugar is None
        assert nutr.protein is None
        assert nutr.fiber is None
        assert nutr.salt is None
        assert nutr.sodium is None

    def test_explicit_zero_distinct_from_none(self):
        declared_zero = Nutrition100g(sugar=0.0, salt=0.0)
        undeclared = Nutrition100g()

        assert declared_zero.sugar == 0.0
        assert declared_zero.sugar is not None
        assert undeclared.sugar is None

    def test_negative_nutrient_values_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            Nutrition100g(calories=-10.0)
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            Nutrition100g(sugar=-0.5)
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            Nutrition100g(salt=-1.0)
        assert "greater than or equal to 0" in str(exc_info.value)


class TestScoreRangeValidation:
    """Tests for ScoreRange bounded range validation and frozen immutability."""

    def test_valid_score_range(self):
        sr = ScoreRange(min_score=35.0, max_score=75.0)
        assert sr.min_score == 35.0
        assert sr.max_score == 75.0

    def test_identical_min_and_max(self):
        sr = ScoreRange(min_score=50.0, max_score=50.0)
        assert sr.min_score == 50.0
        assert sr.max_score == 50.0

    def test_min_greater_than_max_rejected(self):
        with pytest.raises(ValueError) as exc_info:
            ScoreRange(min_score=80.0, max_score=40.0)
        assert "min_score" in str(exc_info.value) and "greater than max_score" in str(exc_info.value)

    def test_out_of_bounds_score_rejected(self):
        with pytest.raises(ValidationError):
            ScoreRange(min_score=-5.0, max_score=50.0)
        with pytest.raises(ValidationError):
            ScoreRange(min_score=50.0, max_score=105.0)

    def test_min_score_assignment_rejected_frozen(self):
        sr = ScoreRange(min_score=20.0, max_score=60.0)
        with pytest.raises(ValidationError) as exc_info:
            sr.min_score = 30.0
        assert "frozen" in str(exc_info.value).lower()

    def test_max_score_assignment_rejected_frozen(self):
        sr = ScoreRange(min_score=20.0, max_score=60.0)
        with pytest.raises(ValidationError) as exc_info:
            sr.max_score = 70.0
        assert "frozen" in str(exc_info.value).lower()

    def test_creating_new_score_range_with_updated_boundaries(self):
        sr_initial = ScoreRange(min_score=20.0, max_score=40.0)
        # Create a new immutable instance with updated boundaries
        sr_updated = ScoreRange(min_score=50.0, max_score=70.0)
        assert sr_updated.min_score == 50.0
        assert sr_updated.max_score == 70.0
        # Original remains completely unchanged
        assert sr_initial.min_score == 20.0
        assert sr_initial.max_score == 40.0


class TestMutableDefaultsIsolation:
    """Verifies that Field(default_factory=...) prevents cross-instance state leakage."""

    def test_alternate_barcodes_isolation(self):
        p1 = CanonicalProduct(canonical_id="P1", barcode="111", product_name="Product 1")
        p2 = CanonicalProduct(canonical_id="P2", barcode="222", product_name="Product 2")

        p1.alternate_barcodes.append("ALT-111")
        assert "ALT-111" in p1.alternate_barcodes
        assert p2.alternate_barcodes == []

    def test_field_provenance_isolation(self):
        p1 = CanonicalProduct(canonical_id="P1", barcode="111", product_name="Product 1")
        p2 = CanonicalProduct(canonical_id="P2", barcode="222", product_name="Product 2")

        p1.field_provenance["calories"] = FieldProvenance(
            source=DataProvenance.OFF_API_V3,
            confidence=DataConfidence.MEDIUM,
        )
        assert "calories" in p1.field_provenance
        assert p2.field_provenance == {}


class TestSaltAndSodiumConversion:
    """Tests regulatory salt = sodium * 2.5 conversion and precedence."""

    def test_calculate_salt_from_sodium(self):
        assert calculate_salt_from_sodium(None) is None
        assert calculate_salt_from_sodium(0.4) == 1.0
        assert calculate_salt_from_sodium(0.48) == 1.2
        assert calculate_salt_from_sodium(0.0) == 0.0

    def test_resolve_effective_salt_prefers_declared_salt(self):
        nutr = Nutrition100g(salt=1.5, sodium=0.2)
        # Declared salt=1.5 should be used directly
        assert resolve_effective_salt(nutr) == 1.5

    def test_resolve_effective_salt_falls_back_to_sodium(self):
        nutr = Nutrition100g(salt=None, sodium=0.4)
        # 0.4 sodium * 2.5 = 1.0 salt
        assert resolve_effective_salt(nutr) == 1.0

    def test_resolve_effective_salt_neither_declared(self):
        nutr = Nutrition100g(salt=None, sodium=None)
        assert resolve_effective_salt(nutr) is None


class TestAssessmentStatusDerivation:
    """Tests dynamic derivation of ASSESSABLE, PARTIALLY_ASSESSABLE, and UNASSESSABLE."""

    def test_full_mandatory_nutrients_is_assessable(self):
        # 4 mandatory: calories, sugar, fat, salt
        p = CanonicalProduct(
            canonical_id="FULL-1",
            barcode="10000001",
            product_name="Complete Product",
            nutrition=Nutrition100g(calories=250.0, sugar=4.0, fat=8.0, salt=0.5),
            ingredients="Wheat, water, salt",
        )
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.HIGH

    def test_full_mandatory_via_sodium_is_assessable(self):
        # 4 mandatory: calories, sugar, fat, sodium (converts to salt)
        p = CanonicalProduct(
            canonical_id="FULL-2",
            barcode="10000002",
            product_name="Sodium-based Product",
            nutrition=Nutrition100g(calories=250.0, sugar=4.0, fat=8.0, sodium=0.2),
            ingredients="Wheat, water",
        )
        assert p.assessment_status == AssessmentStatus.ASSESSABLE

    def test_one_to_three_mandatory_is_partially_assessable(self):
        # Only calories and fat present; sugar and salt are missing
        p = CanonicalProduct(
            canonical_id="PARTIAL-1",
            barcode="20000001",
            product_name="Partial Product",
            nutrition=Nutrition100g(calories=300.0, fat=12.0),
        )
        assert p.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE
        assert p.overall_confidence == DataConfidence.LOW

    def test_zero_mandatory_is_unassessable(self):
        # Blank nutrition
        p = CanonicalProduct(
            canonical_id="ZERO-1",
            barcode="30000001",
            product_name="Empty Product",
            nutrition=Nutrition100g(),
        )
        assert p.assessment_status == AssessmentStatus.UNASSESSABLE
        assert p.overall_confidence == DataConfidence.UNVERIFIED


class TestConfidenceDerivation:
    """Tests dynamic derivation of HIGH, MEDIUM, LOW, and UNVERIFIED confidence."""

    def test_assessable_with_ingredients_is_high_confidence(self):
        p = CanonicalProduct(
            canonical_id="C-HIGH",
            barcode="40000001",
            product_name="High Confidence Item",
            nutrition=Nutrition100g(calories=200.0, sugar=3.0, fat=5.0, salt=0.2),
            ingredients="Oats, honey, salt",
        )
        assert p.overall_confidence == DataConfidence.HIGH

    def test_assessable_without_ingredients_is_medium_confidence(self):
        p = CanonicalProduct(
            canonical_id="C-MED",
            barcode="40000002",
            product_name="Medium Confidence Item",
            nutrition=Nutrition100g(calories=200.0, sugar=3.0, fat=5.0, salt=0.2),
            ingredients=None,
        )
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.MEDIUM

    def test_partial_is_low_confidence(self):
        p = CanonicalProduct(
            canonical_id="C-LOW",
            barcode="40000003",
            product_name="Low Confidence Item",
            nutrition=Nutrition100g(calories=200.0),  # Missing sugar, fat, salt
        )
        assert p.overall_confidence == DataConfidence.LOW

    def test_unassessable_is_unverified_confidence(self):
        p = CanonicalProduct(
            canonical_id="C-UNV",
            barcode="40000004",
            product_name="Unverified Item",
            nutrition=Nutrition100g(),
        )
        assert p.overall_confidence == DataConfidence.UNVERIFIED


class TestCompletenessMetricsDistinction:
    """Verifies that nutrition completeness and ingredient completeness are tracked separately."""

    def test_zero_nutrition_with_rich_ingredients(self):
        p = CanonicalProduct(
            canonical_id="COMP-1",
            barcode="50000001",
            product_name="Ingredients Only Product",
            nutrition=Nutrition100g(),
            ingredients="Enriched flour, palm olein, salt, emulsifier 322, turmeric extract",
            additives="en:e322,en:e100",
        )
        assert p.nutrition_completeness == 0.0
        assert p.ingredients_completeness == 1.0  # 0.7 ingredients + 0.3 additives
        assert p.assessment_status == AssessmentStatus.UNASSESSABLE
        assert p.overall_confidence == DataConfidence.UNVERIFIED

    def test_full_nutrition_with_zero_ingredients(self):
        p = CanonicalProduct(
            canonical_id="COMP-2",
            barcode="50000002",
            product_name="Nutrition Only Product",
            nutrition=Nutrition100g(
                calories=150.0,
                fat=2.0,
                saturated_fat=0.5,
                carbs=25.0,
                sugar=5.0,
                protein=4.0,
                fiber=2.0,
                salt=0.3,
            ),
            ingredients=None,
            additives=None,
        )
        assert p.nutrition_completeness == 1.0  # 8/8 core nutrients present
        assert p.ingredients_completeness == 0.0
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.MEDIUM


class TestDietSpecificAssessability:
    """Tests diet-specific mandatory criteria checks."""

    def test_diabetic_requires_sugar_and_carbs(self):
        # Has sugar but missing carbs
        p1 = CanonicalProduct(
            canonical_id="D-1",
            barcode="60000001",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, salt=0.2, carbs=None),
            ingredients="Wheat, salt",
        )
        ok1, reason1 = is_assessable_for_diet(p1, "diabetic")
        assert ok1 is False
        assert "carbs" in reason1

        # Has both sugar and carbs
        p2 = CanonicalProduct(
            canonical_id="D-2",
            barcode="60000002",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, salt=0.2, carbs=25.0),
            ingredients="Wheat, salt",
        )
        ok2, reason2 = is_assessable_for_diet(p2, "diabetic")
        assert ok2 is True
        assert reason2 is None

    def test_low_sodium_requires_salt_or_sodium(self):
        # Missing both salt and sodium
        p1 = CanonicalProduct(
            canonical_id="S-1",
            barcode="70000001",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, salt=None, sodium=None),
        )
        ok1, reason1 = is_assessable_for_diet(p1, "low_sodium")
        assert ok1 is False
        assert "sodium/salt" in reason1

        # Has sodium
        p2 = CanonicalProduct(
            canonical_id="S-2",
            barcode="70000002",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, sodium=0.1),
        )
        ok2, reason2 = is_assessable_for_diet(p2, "low_sodium")
        assert ok2 is True

    def test_vegan_and_vegetarian_require_ingredients(self):
        p_no_ingr = CanonicalProduct(
            canonical_id="V-1",
            barcode="80000001",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, salt=0.5),
            ingredients=None,
        )
        ok_veg, r_veg = is_assessable_for_diet(p_no_ingr, "vegan")
        assert ok_veg is False
        assert "ingredients" in r_veg

        p_with_ingr = CanonicalProduct(
            canonical_id="V-2",
            barcode="80000002",
            product_name="Product",
            nutrition=Nutrition100g(calories=200.0, sugar=5.0, fat=5.0, salt=0.5),
            ingredients="Rolled oats, almond milk",
        )
        ok_veg2, r_veg2 = is_assessable_for_diet(p_with_ingr, "vegan")
        assert ok_veg2 is True


class TestMutationSafety:
    """Verifies that mutating product or nested nutrition/ingredients re-derives all fields without staleness."""

    def test_mutating_nutrition_field_changes_assessment_status(self):
        # Starts partially assessable (only calories and fat)
        p = CanonicalProduct(
            canonical_id="MUT-STATUS",
            barcode="90000010",
            product_name="Mutation Test Item",
            nutrition=Nutrition100g(calories=200.0, fat=5.0),
        )
        assert p.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE

        # Add remaining mandatory nutrients
        p.nutrition.sugar = 3.0
        p.nutrition.salt = 0.4
        assert p.assessment_status == AssessmentStatus.ASSESSABLE

        # Remove mandatory nutrient
        p.nutrition.sugar = None
        assert p.assessment_status == AssessmentStatus.PARTIALLY_ASSESSABLE

    def test_mutating_nutrition_field_changes_nutrition_completeness(self):
        p = CanonicalProduct(
            canonical_id="MUT-COMP",
            barcode="90000011",
            product_name="Completeness Mutation Item",
            nutrition=Nutrition100g(),
        )
        assert p.nutrition_completeness == 0.0

        p.nutrition.calories = 150.0
        assert p.nutrition_completeness == round(1.0 / TOTAL_NUTRITION_SLOTS, 2)

        p.nutrition.fat = 2.0
        p.nutrition.saturated_fat = 0.5
        p.nutrition.carbs = 20.0
        p.nutrition.sugar = 5.0
        p.nutrition.protein = 4.0
        p.nutrition.fiber = 2.0
        p.nutrition.salt = 0.3
        assert p.nutrition_completeness == 1.0

    def test_adding_ingredients_changes_ingredients_completeness_and_confidence(self):
        p = CanonicalProduct(
            canonical_id="MUT-INGR",
            barcode="90000012",
            product_name="Ingredient Mutation Item",
            nutrition=Nutrition100g(calories=100.0, fat=2.0, sugar=1.0, salt=0.1),
            ingredients=None,
        )
        # Assessable but missing ingredients -> MEDIUM confidence
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.ingredients_completeness == 0.0
        assert p.overall_confidence == DataConfidence.MEDIUM

        # Add ingredients -> confidence transitions to HIGH
        p.ingredients = "Organic rolled oats, almond milk, sea salt"
        assert p.ingredients_completeness == 0.7
        assert p.overall_confidence == DataConfidence.HIGH

        # Add additives
        p.additives = "en:e322"
        assert p.ingredients_completeness == 1.0

    def test_direct_assignment_to_nutrition_rederives_all_fields(self):
        p = CanonicalProduct(
            canonical_id="MUT-ASSIGN",
            barcode="90000013",
            product_name="Direct Assignment Item",
            nutrition=Nutrition100g(),
        )
        assert p.assessment_status == AssessmentStatus.UNASSESSABLE
        assert p.overall_confidence == DataConfidence.UNVERIFIED
        assert p.nutrition_completeness == 0.0

        # Replace entire nutrition object directly
        p.nutrition = Nutrition100g(
            calories=350.0,
            fat=10.0,
            saturated_fat=3.0,
            carbs=50.0,
            sugar=12.0,
            protein=6.0,
            fiber=3.0,
            salt=1.0,
        )
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.MEDIUM
        assert p.nutrition_completeness == 1.0

    def test_no_stale_assessment_values_persist_after_unassessable_to_assessable(self):
        p = CanonicalProduct(
            canonical_id="MUT-STALE",
            barcode="90000014",
            product_name="Stale Assessment Item",
            nutrition=Nutrition100g(),
        )
        assert p.assessment_status == AssessmentStatus.UNASSESSABLE
        assert p.overall_confidence == DataConfidence.UNVERIFIED

        # Mutate nested fields one by one to reach ASSESSABLE
        p.nutrition.calories = 250.0
        p.nutrition.sugar = 8.0
        p.nutrition.fat = 6.0
        p.nutrition.salt = 0.5
        p.ingredients = "Whole wheat flour, water, yeast, salt"

        # Assessment values must NOT remain UNASSESSABLE
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.HIGH
        assert p.nutrition_completeness >= 0.5
        assert p.ingredients_completeness >= 0.7

    def test_derive_product_assessment_backward_compatibility(self):
        p = CanonicalProduct(
            canonical_id="MUT-COMPAT",
            barcode="90000015",
            product_name="Backward Compat Item",
            nutrition=Nutrition100g(calories=200.0, fat=5.0),
        )
        p.nutrition.sugar = 4.0
        p.nutrition.salt = 0.5
        p.ingredients = "Whole wheat, salt"

        returned = derive_product_assessment(p)
        assert returned is p
        assert p.assessment_status == AssessmentStatus.ASSESSABLE
        assert p.overall_confidence == DataConfidence.HIGH


class TestPydanticAssignmentValidation:
    """Verifies that validate_assignment=True prevents invalid post-init mutations."""

    def test_nutrition_negative_assignment_raises_validation_error(self):
        nutr = Nutrition100g(calories=100.0, fat=5.0)

        with pytest.raises(ValidationError) as exc_info:
            nutr.calories = -10.0
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            nutr.fat = -0.1
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            nutr.sugar = -5.0
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            nutr.salt = -0.01
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            nutr.sodium = -1.0
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_score_range_assignment_validation(self):
        sr = ScoreRange(min_score=20.0, max_score=60.0)

        # Frozen instance rejects direct mutation of min_score
        with pytest.raises(ValidationError) as exc_info:
            sr.min_score = 30.0
        assert "frozen" in str(exc_info.value).lower()

        # Frozen instance rejects direct mutation of max_score
        with pytest.raises(ValidationError) as exc_info:
            sr.max_score = 80.0
        assert "frozen" in str(exc_info.value).lower()

    def test_nested_nutrition_assignment_validation(self):
        prod = CanonicalProduct(
            canonical_id="VAL-NEST",
            barcode="90000020",
            product_name="Nested Validation Item",
            nutrition=Nutrition100g(calories=200.0, fat=5.0),
        )

        with pytest.raises(ValidationError) as exc_info:
            prod.nutrition.calories = -25.0
        assert "greater than or equal to 0" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            prod.nutrition.sugar = -1.0
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_canonical_product_assignment_validation(self):
        prod = CanonicalProduct(
            canonical_id="VAL-PROD",
            barcode="90000021",
            product_name="Product Assignment Item",
        )

        # Replacing nutrition with invalid type raises ValidationError
        with pytest.raises(ValidationError):
            prod.nutrition = "invalid_string_not_nutrition"

        # Cannot directly overwrite computed properties without setters
        with pytest.raises(AttributeError):
            prod.assessment_status = AssessmentStatus.ASSESSABLE

        with pytest.raises(AttributeError):
            prod.overall_confidence = DataConfidence.HIGH

        with pytest.raises(AttributeError):
            prod.nutrition_completeness = 1.0

        with pytest.raises(AttributeError):
            prod.ingredients_completeness = 1.0

    def test_field_provenance_assignment_validation(self):
        fp = FieldProvenance(
            source=DataProvenance.LOCAL_DB,
            confidence=DataConfidence.MEDIUM,
        )

        with pytest.raises(ValidationError):
            fp.confidence = "INVALID_CONFIDENCE_LEVEL"

        with pytest.raises(ValidationError):
            fp.source = "NON_EXISTENT_PROVENANCE"


class TestFieldProvenanceConfidence:
    """Verifies FieldProvenance confidence requirements and safeguards."""

    def test_field_provenance_requires_explicit_confidence(self):
        with pytest.raises(ValidationError) as exc_info:
            FieldProvenance(source=DataProvenance.OFF_API_V3)
        assert "confidence" in str(exc_info.value)

    def test_field_provenance_explicit_assignment(self):
        fp = FieldProvenance(
            source=DataProvenance.OFF_API_V3,
            confidence=DataConfidence.LOW,
        )
        assert fp.confidence == DataConfidence.LOW
        assert fp.source == DataProvenance.OFF_API_V3

    def test_confidence_cannot_accidentally_default_to_high(self):
        # Instantiation without confidence must fail, never silently default to HIGH
        with pytest.raises(ValidationError):
            FieldProvenance(source=DataProvenance.OCR)


class TestCompletenessCalculationInvariants:
    """Regression tests locking the five completeness invariants."""

    def test_invariant_1_strictly_eight_slots(self):
        """Invariant 1: Exactly 8 core tracking slots are enforced."""
        assert TOTAL_NUTRITION_SLOTS == 8

        # Empty nutrition -> 0.0
        nutr_empty = Nutrition100g()
        assert calculate_nutrition_completeness(nutr_empty) == 0.0

        # All 8 slots populated -> exactly 1.0
        nutr_full = Nutrition100g(
            calories=200.0,
            fat=10.0,
            saturated_fat=2.0,
            carbs=30.0,
            sugar=5.0,
            protein=8.0,
            fiber=4.0,
            salt=1.0,
        )
        assert calculate_nutrition_completeness(nutr_full) == 1.0

    def test_invariant_2_salt_and_sodium_count_as_one_logical_dimension(self):
        """Invariant 2: Salt and sodium count as ONE logical dimension."""
        # 7 other slots missing, only salt present -> 1/8
        nutr_salt_only = Nutrition100g(salt=1.0)
        comp_salt = calculate_nutrition_completeness(nutr_salt_only)
        assert comp_salt == round(1.0 / TOTAL_NUTRITION_SLOTS, 2)

        # 7 other slots missing, only sodium present -> 1/8
        nutr_sodium_only = Nutrition100g(sodium=0.4)
        comp_sodium = calculate_nutrition_completeness(nutr_sodium_only)
        assert comp_sodium == round(1.0 / TOTAL_NUTRITION_SLOTS, 2)

        # 7 other slots missing, BOTH salt and sodium present -> still exactly 1/8
        nutr_both = Nutrition100g(salt=1.0, sodium=0.4)
        comp_both = calculate_nutrition_completeness(nutr_both)
        assert comp_both == round(1.0 / TOTAL_NUTRITION_SLOTS, 2)
        assert comp_both == comp_salt == comp_sodium

    def test_invariant_3_sodium_only_counts_as_sodium_salt_dimension(self):
        """Invariant 3: Products with only sodium count as having the sodium/salt dimension."""
        nutr = Nutrition100g(
            calories=100.0,
            fat=5.0,
            sugar=2.0,
            sodium=0.2,  # sodium only, salt is None
        )
        # 4 mandatory nutrients present (calories, fat, sugar, sodium-converted salt)
        prod = CanonicalProduct(
            canonical_id="SODIUM-ONLY",
            barcode="90000030",
            product_name="Sodium Item",
            nutrition=nutr,
        )
        assert prod.assessment_status == AssessmentStatus.ASSESSABLE
        assert calculate_nutrition_completeness(nutr) == round(4.0 / TOTAL_NUTRITION_SLOTS, 2)

    def test_invariant_4_salt_and_sodium_both_present_never_exceeds_one(self):
        """Invariant 4: Populating all 7 nutrients plus BOTH salt and sodium does not exceed 1.0."""
        nutr_nine_fields = Nutrition100g(
            calories=250.0,
            fat=8.0,
            saturated_fat=2.0,
            carbs=40.0,
            sugar=10.0,
            protein=5.0,
            fiber=3.0,
            salt=1.2,
            sodium=0.48,  # Both salt and sodium present
        )
        completeness = calculate_nutrition_completeness(nutr_nine_fields)
        assert completeness == 1.0
        assert completeness <= 1.0

        prod = CanonicalProduct(
            canonical_id="BOTH-SALT-SODIUM",
            barcode="90000031",
            product_name="Dual Salt Sodium Item",
            nutrition=nutr_nine_fields,
        )
        assert prod.nutrition_completeness == 1.0

    def test_invariant_5_direct_salt_always_precedes_converted_sodium(self):
        """Invariant 5: Direct package declared salt always takes precedence over converted sodium."""
        # salt is 2.0g, sodium is 0.1g (which would convert to 0.25g salt)
        nutr = Nutrition100g(salt=2.0, sodium=0.1)
        effective = resolve_effective_salt(nutr)
        assert effective == 2.0
        assert effective != calculate_salt_from_sodium(0.1)

        # When salt is None, converted sodium is used
        nutr_fallback = Nutrition100g(salt=None, sodium=0.1)
        assert resolve_effective_salt(nutr_fallback) == 0.25
