import pytest

from services.product_search import get_indian_dataset_df
from services.recommendation_engine import (
    get_healthier_alternatives,
    get_static_candidates,
    reset_static_candidates,
    infer_food_category,
)


def test_static_candidates_indexing():
    """Verify that static candidate index dynamically matches the source dataset and pre-scores candidates."""
    reset_static_candidates()
    source_df = get_indian_dataset_df()
    assert source_df is not None and not source_df.empty, "Source Indian packaged foods dataset must be available"

    # Derive expected candidate count and names dynamically from valid dataset rows
    expected_names = [
        str(name).strip() for name in source_df["product_name"].dropna() if str(name).strip()
    ]
    expected_count = len(expected_names)
    assert expected_count > 0, "Source dataset must contain at least one valid product"

    candidates = get_static_candidates()
    assert len(candidates) == expected_count, (
        f"Candidate count ({len(candidates)}) does not match expected source dataset count ({expected_count})"
    )

    # Verify that the static candidate cache contains all valid candidates from the source dataset with no duplicates
    cached_names = [c.product_name for c in candidates]
    assert set(cached_names) == set(expected_names), "Static candidate cache must contain all expected product names"
    assert len(cached_names) == len(set(cached_names)), "Static candidate cache contains duplicate products"

    # Candidates must have pre-computed scores and eligibility flags
    for c in candidates:
        assert c.product_name
        assert c.category
        if c.is_eligible:
            assert c.decision not in ("AVOID", "UNASSESSABLE", "INSUFFICIENT_DATA")


def test_recommendations_cached_structural_integrity():
    """Verify structural integrity, candidate pre-indexing, and functional correctness without brittle timing."""
    source_df = get_indian_dataset_df()
    assert source_df is not None and not source_df.empty, "Source Indian packaged foods dataset must be available"
    expected_count = len([str(n).strip() for n in source_df["product_name"].dropna() if str(n).strip()])

    # 1. Static candidates are indexed and available matching dynamic source count
    candidates = get_static_candidates()
    assert isinstance(candidates, list)
    assert len(candidates) == expected_count

    # 2. Candidate fields are correctly populated
    eligible_count = 0
    for c in candidates:
        assert c.product_name and len(c.product_name) > 0
        assert c.category and len(c.category) > 0
        assert c.calories is not None
        assert c.health_score is not None
        assert c.decision in ("SAFE", "MODERATE", "AVOID", "UNASSESSABLE", "INSUFFICIENT_DATA")
        if c.is_eligible:
            eligible_count += 1
            assert c.decision in ("SAFE", "MODERATE")
    assert eligible_count > 0, "Expected at least one eligible candidate"

    # 3. Recommendation engine returns functionally valid and correctly filtered alternatives
    test_product = "Lay's Classic Salted Potato Chips"
    test_nutrition = {
        "calories": 540,
        "fat": 34.0,
        "sugar": 1.5,
        "salt": 1.2,
        "protein": 6.5,
        "fiber": 3.0,
        "carbs": 52.0,
    }

    recs = get_healthier_alternatives(test_product, test_nutrition, limit=3)
    assert isinstance(recs, list)
    assert len(recs) <= 3
    for r in recs:
        assert "product_name" in r
        assert "category" in r
        assert "improvement_score" in r
        assert r["improvement_score"] > 0
        assert "reason" in r
        assert r["decision"] in ("SAFE", "MODERATE")
        assert "nutrition" in r
        assert isinstance(r["nutrition"], dict)


def test_recommendations_deterministic_ranking():
    """Verify deterministic ranking and semantic consistency across multiple calls."""
    test_product = "Maggi 2-Minute Masala Noodles"
    test_nutrition = {
        "calories": 427,
        "fat": 15.7,
        "sugar": 2.2,
        "salt": 2.1,
        "protein": 8.0,
        "fiber": 2.0,
        "carbs": 63.5,
    }

    r1 = get_healthier_alternatives(test_product, test_nutrition, limit=3)
    r2 = get_healthier_alternatives(test_product, test_nutrition, limit=3)

    assert len(r1) == len(r2)
    for a, b in zip(r1, r2):
        assert a["product_name"] == b["product_name"]
        assert a["improvement_score"] == b["improvement_score"]
        assert a["reason"] == b["reason"]
        assert a["decision"] in ("SAFE", "MODERATE")


def test_recommendations_empty_input_handling():
    """Verify graceful empty handling for invalid or empty inputs."""
    assert get_healthier_alternatives("", {}) == []
    assert get_healthier_alternatives("Some Product", {}) == []
    assert get_healthier_alternatives(None, {"calories": 100}) == []
