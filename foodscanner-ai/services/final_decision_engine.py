from __future__ import annotations

from typing import Any, Optional

from services.scoring_engine import _to_float, get_default_scoring_engine

__all__ = ["_to_float", "compute_final_decision"]


def compute_final_decision(
    health_score_result: dict,
    remaining_calories: float,
    product_calories: float,
) -> dict:
    """Compute final recommendation decision delegating to UniversalScoringEngine."""
    engine = get_default_scoring_engine()
    return engine.evaluate_final_decision(
        health_score_result=health_score_result,
        remaining_calories=remaining_calories,
        product_calories=product_calories,
    )
