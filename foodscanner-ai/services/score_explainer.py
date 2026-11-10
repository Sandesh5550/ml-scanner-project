from __future__ import annotations

from typing import Any

from services.scoring_engine import get_default_scoring_engine

__all__ = ["explain_score"]


def explain_score(product: dict[str, Any], diet_type: str | None) -> dict[str, Any]:
    """Provide detailed score breakdown and explanation delegating to UniversalScoringEngine."""
    engine = get_default_scoring_engine()
    return engine.explain_product_score(product, diet_type=diet_type)
