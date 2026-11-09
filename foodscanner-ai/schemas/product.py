from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

TOTAL_NUTRITION_SLOTS: int = 8


class DataProvenance(str, Enum):
    """Source provenance tracking origin of product data."""
    LOCAL_DB = "local_db"
    ADMIN_VERIFIED = "admin_verified"
    OFF_API_V3 = "openfoodfacts_api_v3"
    OFF_API_V2 = "openfoodfacts_api_v2"
    OFF_CSV = "openfoodfacts_csv"
    INDIAN_CSV = "indian_dataset_csv"
    OCR = "ocr_extracted"
    MANUAL = "manual_entry"


class DataConfidence(str, Enum):
    """Data quality and confidence level derived from validated disclosures."""
    HIGH = "HIGH"               # Complete mandatory nutrition + ingredients present
    MEDIUM = "MEDIUM"           # Complete mandatory nutrition; non-mandatory or ingredients missing
    LOW = "LOW"                 # Mandatory nutrition partially missing
    UNVERIFIED = "UNVERIFIED"   # Identity only; no verified nutrition disclosed


class AssessmentStatus(str, Enum):
    """Health score assessability status based on mandatory nutrient presence."""
    ASSESSABLE = "ASSESSABLE"
    PARTIALLY_ASSESSABLE = "PARTIALLY_ASSESSABLE"
    UNASSESSABLE = "UNASSESSABLE"
    UNVERIFIED = "UNVERIFIED"


class FieldProvenance(BaseModel):
    """Tracks source, timestamp, and confidence for a single product field."""
    model_config = ConfigDict(validate_assignment=True)

    source: DataProvenance
    confidence: DataConfidence
    timestamp: Optional[datetime] = None


class Nutrition100g(BaseModel):
    """Normalized nutritional values per 100g.

    All values are strictly nullable and validated non-negative.
    Missing disclosures remain None and are NEVER converted to zero.
    """
    model_config = ConfigDict(validate_assignment=True)

    calories: Optional[float] = Field(None, ge=0.0, description="Energy in kcal per 100g")
    fat: Optional[float] = Field(None, ge=0.0, description="Total fat in grams per 100g")
    saturated_fat: Optional[float] = Field(None, ge=0.0, description="Saturated fat in grams per 100g")
    trans_fat: Optional[float] = Field(None, ge=0.0, description="Trans fat in grams per 100g")
    carbs: Optional[float] = Field(None, ge=0.0, description="Total carbohydrates in grams per 100g")
    sugar: Optional[float] = Field(None, ge=0.0, description="Sugars in grams per 100g")
    protein: Optional[float] = Field(None, ge=0.0, description="Protein in grams per 100g")
    fiber: Optional[float] = Field(None, ge=0.0, description="Dietary fiber in grams per 100g")
    salt: Optional[float] = Field(None, ge=0.0, description="Salt in grams per 100g")
    sodium: Optional[float] = Field(None, ge=0.0, description="Sodium in grams per 100g")


class ScoreRange(BaseModel):
    """Deterministic score range for partially declared nutritional profiles."""
    model_config = ConfigDict(frozen=True)

    min_score: float = Field(..., ge=0.0, le=100.0, description="Worst-case score assuming undeclared mandatory nutrients have maximum penalty")
    max_score: float = Field(..., ge=0.0, le=100.0, description="Best-case score assuming undeclared mandatory nutrients have zero penalty")

    @model_validator(mode="after")
    def _validate_score_bounds(self) -> ScoreRange:
        if self.min_score > self.max_score:
            raise ValueError(f"min_score ({self.min_score}) cannot be greater than max_score ({self.max_score})")
        return self


def calculate_salt_from_sodium(sodium: Optional[float]) -> Optional[float]:
    """Convert sodium (in grams) to salt (in grams) using the regulatory factor 2.5."""
    if sodium is None:
        return None
    try:
        val = float(sodium)
        if val < 0.0:
            return None
        return round(val * 2.5, 3)
    except (TypeError, ValueError):
        return None


def resolve_effective_salt(nutrition: Nutrition100g) -> Optional[float]:
    """Resolve salt content: prefer declared salt, fall back to sodium * 2.5.

    Invariants:
    - Direct package declared salt always takes precedence over converted sodium.
    - If salt is None, converted sodium is used (if available and non-negative).
    - If both are None, returns None.
    """
    if nutrition.salt is not None:
        return nutrition.salt
    if nutrition.sodium is not None:
        return calculate_salt_from_sodium(nutrition.sodium)
    return None


def calculate_nutrition_completeness(nutrition: Nutrition100g) -> float:
    """Calculate the ratio (0.0 to 1.0) of populated core nutrients.

    Core tracking slots (strictly 8 slots):
    1. calories
    2. fat
    3. saturated_fat
    4. carbs
    5. sugar
    6. protein
    7. fiber
    8. salt (or sodium) - treated as ONE single logical dimension
    """
    has_salt_or_sodium = resolve_effective_salt(nutrition) is not None
    slots = [
        nutrition.calories is not None,
        nutrition.fat is not None,
        nutrition.saturated_fat is not None,
        nutrition.carbs is not None,
        nutrition.sugar is not None,
        nutrition.protein is not None,
        nutrition.fiber is not None,
        has_salt_or_sodium,
    ]
    assert len(slots) == TOTAL_NUTRITION_SLOTS, f"Expected exactly {TOTAL_NUTRITION_SLOTS} slots"
    present_count = sum(1 for present in slots if present)
    return min(1.0, round(present_count / float(TOTAL_NUTRITION_SLOTS), 2))


def calculate_ingredients_completeness(
    ingredients: Optional[str],
    additives: Optional[str] = None,
) -> float:
    """Calculate ingredients and additives disclosure completeness (0.0 to 1.0).

    Independent of nutrition completeness:
    - Meaningful ingredients text (>=5 characters) provides 0.7.
    - Explicit additives disclosure or analyzed tags provides up to 0.3.
    """
    score = 0.0
    if ingredients is not None and len(ingredients.strip()) >= 5:
        score += 0.7
    if additives is not None and len(additives.strip()) >= 2:
        score += 0.3
    return min(1.0, round(score, 2))


def is_assessable_for_diet(
    product: CanonicalProduct,
    diet_type: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """Determine whether product disclosures satisfy diet-specific mandatory criteria.

    Returns:
        (is_assessable, rejection_reason_if_not)
    """
    if not diet_type:
        is_ok = product.assessment_status == AssessmentStatus.ASSESSABLE
        reason = None if is_ok else "Missing mandatory general nutrition disclosures"
        return is_ok, reason

    dt = diet_type.strip().lower()

    if dt == "diabetic":
        has_sugar = product.nutrition.sugar is not None
        has_carbs = product.nutrition.carbs is not None
        if not (has_sugar and has_carbs):
            missing = []
            if not has_sugar:
                missing.append("sugar")
            if not has_carbs:
                missing.append("carbs")
            return False, f"Missing {', '.join(missing)} disclosure required for diabetic assessment"
        return True, None

    if dt == "low_sodium":
        has_salt = resolve_effective_salt(product.nutrition) is not None
        if not has_salt:
            return False, "Missing sodium/salt disclosure required for low-sodium assessment"
        return True, None

    if dt in ("vegan", "vegetarian"):
        has_ingr = product.ingredients is not None and len(product.ingredients.strip()) >= 3
        if not has_ingr:
            return False, f"Missing ingredients disclosure required for {dt} compliance verification"
        return True, None

    # Unknown or unconstrained diet: general assessability applies
    is_ok = product.assessment_status == AssessmentStatus.ASSESSABLE
    reason = None if is_ok else "Missing mandatory general nutrition disclosures"
    return is_ok, reason


def derive_product_assessment(product: CanonicalProduct) -> CanonicalProduct:
    """Derive assessment_status, overall_confidence, and completeness metrics from validated data.

    With computed properties on CanonicalProduct, assessment metrics are automatically
    and dynamically evaluated upon access. This function is maintained for backward
    compatibility and returns the product directly.
    """
    return product


class CanonicalProduct(BaseModel):
    """Canonical internal product data structure across PRAMAAN.

    Safe mutable defaults are enforced via Field(default_factory=...).
    Assessment status, confidence, and completeness are dynamically computed properties
    derived from validated product disclosures without artificial defaults.
    """
    model_config = ConfigDict(validate_assignment=True)

    canonical_id: str
    barcode: str
    alternate_barcodes: List[str] = Field(default_factory=list)
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    variant_name: Optional[str] = None
    serving_size: Optional[str] = None
    nutrition: Nutrition100g = Field(default_factory=Nutrition100g)
    ingredients: Optional[str] = None
    additives: Optional[str] = None
    nutriscore: Optional[str] = None
    is_nutriscore_predicted: bool = False

    # Provenance Tracking
    primary_provenance: DataProvenance = DataProvenance.LOCAL_DB
    field_provenance: Dict[str, FieldProvenance] = Field(default_factory=dict)

    # Optional Timestamps
    last_fetched_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None

    @computed_field
    @property
    def nutrition_completeness(self) -> float:
        """Dynamically computed ratio (0.0 to 1.0) of populated core nutrients."""
        return calculate_nutrition_completeness(self.nutrition)

    @computed_field
    @property
    def ingredients_completeness(self) -> float:
        """Dynamically computed completeness (0.0 to 1.0) of ingredients disclosure."""
        return calculate_ingredients_completeness(self.ingredients, self.additives)

    @computed_field
    @property
    def assessment_status(self) -> AssessmentStatus:
        """Dynamically derived assessability status based on mandatory general nutrients.

        Mandatory nutrients:
        1. calories
        2. sugar
        3. fat
        4. salt (or sodium via resolve_effective_salt)

        Rules:
        - All 4 present: ASSESSABLE
        - 1 to 3 present: PARTIALLY_ASSESSABLE
        - 0 present: UNASSESSABLE
        """
        n = self.nutrition
        has_cal = n.calories is not None
        has_sug = n.sugar is not None
        has_fat = n.fat is not None
        has_salt = resolve_effective_salt(n) is not None

        mandatory_count = sum(1 for present in [has_cal, has_sug, has_fat, has_salt] if present)
        if mandatory_count == 4:
            return AssessmentStatus.ASSESSABLE
        elif mandatory_count > 0:
            return AssessmentStatus.PARTIALLY_ASSESSABLE
        else:
            return AssessmentStatus.UNASSESSABLE

    @computed_field
    @property
    def overall_confidence(self) -> DataConfidence:
        """Dynamically derived data confidence level.

        Rules:
        - UNASSESSABLE -> UNVERIFIED
        - PARTIALLY_ASSESSABLE -> LOW
        - ASSESSABLE -> HIGH (if ingredients_completeness >= 0.7) else MEDIUM
        """
        status = self.assessment_status
        if status == AssessmentStatus.UNASSESSABLE:
            return DataConfidence.UNVERIFIED
        elif status == AssessmentStatus.PARTIALLY_ASSESSABLE:
            return DataConfidence.LOW
        else:
            return DataConfidence.HIGH if self.ingredients_completeness >= 0.7 else DataConfidence.MEDIUM
