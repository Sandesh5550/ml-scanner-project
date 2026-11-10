"""Unit tests for the pure scoring logic (no database or network needed)."""

import pytest

from services.food_health_score import (
    _to_float,
    compute_diet_aware_score,
    compute_food_health_score,
)

HEALTHY = {"sugar": 1, "salt": 0.1, "fat": 2, "saturated_fat": 0.5,
           "calories": 60, "protein": 12, "fiber": 8}
JUNK = {"sugar": 45, "salt": 2.5, "fat": 35, "saturated_fat": 15,
        "calories": 550, "protein": 2, "fiber": 0}


@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("  ", None), ("abc", None),
    ("3.5", 3.5), (4, 4.0),
])
def test_to_float(raw, expected):
    assert _to_float(raw) == expected


def test_score_is_clamped_between_0_and_100():
    for product in (HEALTHY, JUNK):
        score = compute_food_health_score(product)["health_score"]
        assert 0 <= score <= 100

    # Under Phase 5 policy, empty product disclosures are UNASSESSABLE (null score, INSUFFICIENT_DATA)
    empty_res = compute_food_health_score({})
    assert empty_res["health_score"] is None
    assert empty_res["decision"] == "INSUFFICIENT_DATA"


def test_healthy_product_scores_higher_than_junk():
    healthy = compute_food_health_score(HEALTHY)
    junk = compute_food_health_score(JUNK)
    assert healthy["health_score"] > junk["health_score"]
    assert healthy["decision"] == "SAFE"
    assert junk["decision"] == "AVOID"


def test_reason_lists_main_risk_factors():
    reason = compute_food_health_score(JUNK)["reason"]
    assert "High sugar" in reason
    assert "High salt" in reason


def test_missing_values_do_not_crash():
    result = compute_food_health_score({"sugar": None, "salt": ""})
    assert set(result) == {"health_score", "decision", "reason"}
    assert result["health_score"] is None
    assert result["decision"] == "INSUFFICIENT_DATA"


def test_diet_aware_without_diet_returns_base_score():
    assert compute_diet_aware_score(HEALTHY, None) == compute_food_health_score(HEALTHY)


def test_diabetic_diet_is_never_safe_for_high_sugar():
    product = {**HEALTHY, "sugar": 10}
    result = compute_diet_aware_score(product, "diabetic")
    assert result["decision"] != "SAFE"
