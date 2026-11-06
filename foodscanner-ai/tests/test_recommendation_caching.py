from __future__ import annotations

from unittest.mock import patch
import pandas as pd
import pytest

from services.product_search import get_indian_dataset_df
import services.product_search as ps
from services.recommendation_engine import get_healthier_alternatives


SAMPLE_SNACK_NUTRITION = {
    "calories": 540.0,
    "fat": 35.0,
    "sugar": 2.5,
    "salt": 2.2,
    "protein": 6.0,
    "fiber": 2.0,
    "carbs": 52.0,
}

SAMPLE_CHOCOLATE_NUTRITION = {
    "calories": 550.0,
    "fat": 32.0,
    "sugar": 50.0,
    "salt": 0.2,
    "protein": 7.0,
    "fiber": 2.0,
    "carbs": 55.0,
}


def test_recommendation_reuses_cached_dataset():
    """Verify repeated calls to get_healthier_alternatives reuse the in-memory cached DataFrame without disk reads."""
    # Warm up cache
    df_first = get_indian_dataset_df()
    assert df_first is not None
    assert len(df_first) > 0

    # Ensure cache is populated
    assert ps._INDIAN_DATASET_DF is not None

    # Patch pd.read_csv to ensure zero disk reads during repeated recommendation requests
    with patch("pandas.read_csv") as mock_read_csv:
        alts_1 = get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
        alts_2 = get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
        alts_3 = get_healthier_alternatives("Amul Milk Chocolate", SAMPLE_CHOCOLATE_NUTRITION, limit=3)

        assert mock_read_csv.call_count == 0, f"Expected 0 disk reads, got {mock_read_csv.call_count}"

    assert len(alts_1) == 3
    assert len(alts_2) == 3
    assert len(alts_3) > 0
    assert alts_1 == alts_2


def test_missing_dataset_returns_empty_safely():
    """Verify get_healthier_alternatives returns [] gracefully when get_indian_dataset_df returns None."""
    with patch("services.product_search.get_indian_dataset_df", return_value=None):
        res = get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
        assert res == []


def test_malformed_dataset_returns_empty_safely():
    """Verify get_healthier_alternatives returns [] gracefully when DataFrame lacks product_name column."""
    malformed_df = pd.DataFrame({"some_other_column": [1, 2, 3]})
    with patch("services.product_search.get_indian_dataset_df", return_value=malformed_df):
        res = get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
        assert res == []


def test_recommendation_results_and_ordering_unchanged():
    """Verify recommendation ranking, sorting, filtering, and fields remain strictly consistent."""
    alts = get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
    assert len(alts) == 3

    # Ranking must be strictly descending by improvement_score
    scores = [float(a["improvement_score"]) for a in alts]
    assert scores == sorted(scores, reverse=True)

    # All required contract fields must be present
    for alt in alts:
        assert "product_name" in alt and alt["product_name"] is not None
        assert "brand" in alt
        assert "barcode" in alt
        assert "category" in alt and alt["category"] == "chips_and_snacks"
        assert "health_score" in alt and isinstance(alt["health_score"], (int, float))
        assert "nutriscore" in alt
        assert "nutrition" in alt
        assert "reason" in alt and len(alt["reason"]) > 0
        assert "improvement_score" in alt and alt["improvement_score"] > 0
        assert "key_differences" in alt and isinstance(alt["key_differences"], dict)
        assert alt["source"] == "indian_dataset"

        nutr = alt["nutrition"]
        for k in ["calories", "sugar", "salt", "fat", "protein", "fiber", "carbs"]:
            assert k in nutr

    # Test confectionery category
    alts_choc = get_healthier_alternatives("Amul Milk Chocolate", SAMPLE_CHOCOLATE_NUTRITION, limit=3)
    assert len(alts_choc) > 0
    scores_choc = [float(a["improvement_score"]) for a in alts_choc]
    assert scores_choc == sorted(scores_choc, reverse=True)


def test_recommendation_engine_does_not_modify_cached_dataframe():
    """Verify recommendation evaluation treats the cached DataFrame as strictly read-only."""
    df_cached = get_indian_dataset_df()
    assert df_cached is not None

    cols_before = list(df_cached.columns)
    shape_before = df_cached.shape
    first_row_before = df_cached.iloc[0].to_dict()

    # Run multiple recommendation evaluations
    get_healthier_alternatives("Balaji Wafers Masala", SAMPLE_SNACK_NUTRITION, limit=3)
    get_healthier_alternatives("Amul Milk Chocolate", SAMPLE_CHOCOLATE_NUTRITION, limit=3)
    get_healthier_alternatives("Britannia Bourbon", SAMPLE_SNACK_NUTRITION, limit=3)

    # Verify no columns added, no rows deleted, no values modified
    assert list(df_cached.columns) == cols_before
    assert df_cached.shape == shape_before
    assert df_cached.iloc[0].to_dict() == first_row_before
