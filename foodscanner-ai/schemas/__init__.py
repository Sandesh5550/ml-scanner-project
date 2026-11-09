from __future__ import annotations

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

__all__ = [
    "TOTAL_NUTRITION_SLOTS",
    "AssessmentStatus",
    "CanonicalProduct",
    "DataConfidence",
    "DataProvenance",
    "FieldProvenance",
    "Nutrition100g",
    "ScoreRange",
    "calculate_ingredients_completeness",
    "calculate_nutrition_completeness",
    "calculate_salt_from_sodium",
    "derive_product_assessment",
    "is_assessable_for_diet",
    "resolve_effective_salt",
]
