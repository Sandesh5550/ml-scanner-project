from __future__ import annotations

from typing import Any, Optional

from services.scoring_engine import (
    _additive_risk_penalty,
    _calories_penalty,
    _contains_any,
    _fat_penalty,
    _fiber_bonus,
    _ingredient_risk_penalty,
    _nutriscore_bonus,
    _protein_bonus,
    _salt_penalty,
    _sat_fat_penalty,
    _sugar_penalty,
    _to_float,
    get_default_scoring_engine,
)

__all__ = [
    "_to_float",
    "_sugar_penalty",
    "_salt_penalty",
    "_fat_penalty",
    "_sat_fat_penalty",
    "_calories_penalty",
    "_protein_bonus",
    "_fiber_bonus",
    "_ingredient_risk_penalty",
    "_additive_risk_penalty",
    "_nutriscore_bonus",
    "_contains_any",
    "compute_food_health_score",
    "compute_diet_aware_score",
]


def compute_food_health_score(product: dict) -> dict:
    """Authoritative food health score calculation delegating to UniversalScoringEngine."""
    engine = get_default_scoring_engine()
    assessment = engine.score_product(product, diet_type=None)
    return assessment.to_legacy_health_dict()


def compute_diet_aware_score(product: dict, diet_type: str | None) -> dict:
    """Authoritative diet-aware food health score calculation delegating to UniversalScoringEngine."""
    engine = get_default_scoring_engine()
    assessment = engine.score_product(product, diet_type=diet_type)
    return assessment.to_legacy_health_dict()
