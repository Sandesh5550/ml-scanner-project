import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from services.product_search import search_products, _search_openfoodfacts_remote
from services.off_client import (
    OFFResult,
    OFFSearchResult,
    OFFStatus,
    get_default_off_client,
    reset_default_off_client,
    OpenFoodFactsClient,
    OFFRateLimiter,
)
from database.db_session import get_db, SessionLocal


@pytest.fixture(autouse=True)
def clean_client():
    reset_default_off_client()
    yield
    reset_default_off_client()


def test_remote_search_routes_through_off_client():
    """Verify that _search_openfoodfacts_remote delegates to OpenFoodFactsClient.search with 1.5s timeout."""
    off_client = get_default_off_client()
    mock_res = OFFSearchResult(
        status=OFFStatus.SUCCESS,
        query="organic tea",
        products=[{
            "barcode": "8901234567890",
            "product_name": "Organic Green Tea",
            "brand": "Tata Tea",
            "nutriscore": "a",
            "ingredients": "Green tea leaves",
            "nutrition": {"calories": 0.0, "sugar": 0.0, "salt": 0.0, "fat": 0.0, "protein": 0.0, "fiber": 0.0, "carbs": 0.0},
        }],
    )

    with patch.object(off_client, "search", return_value=mock_res) as mock_search:
        results = _search_openfoodfacts_remote("organic tea", limit=5)
        assert mock_search.called
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["timeout"] == 1.5
        assert call_kwargs["limit"] == 5
        assert len(results) == 1
        assert results[0]["product_name"] == "Organic Green Tea"
        assert results[0]["source"] == "openfoodfacts_api"


def test_remote_search_failure_preserves_local_results():
    """Verify that if remote search fails or is rate limited, search_products still returns local DB/CSV results."""
    db: Session = SessionLocal()
    try:
        off_client = get_default_off_client()
        mock_rate_limited = OFFSearchResult(
            status=OFFStatus.RATE_LIMITED,
            query="Amul",
            error="Rate limit budget exhausted for search",
        )

        with patch.object(off_client, "search", return_value=mock_rate_limited):
            results = search_products(db, "Amul", limit=10)
            assert isinstance(results, list)
            # Local results for "Amul" exist in the Indian dataset and local DB
            assert len(results) > 0
            # Ensure none of the results crashed and local sources are present
            sources = {r.get("source") for r in results}
            assert "indian_dataset" in sources or "local_db" in sources or "openfoodfacts_dataset" in sources
    finally:
        db.close()


def test_remote_search_timeout_handles_gracefully():
    """Verify that a remote timeout returns empty list without raising an unhandled exception."""
    off_client = get_default_off_client()
    mock_timeout = OFFSearchResult(
        status=OFFStatus.NETWORK_UNAVAILABLE,
        query="unobtainable_snack",
        error="Timeout on search",
    )

    with patch.object(off_client, "search", return_value=mock_timeout):
        results = _search_openfoodfacts_remote("unobtainable_snack", limit=5)
        assert results == []


def test_search_rate_limiter_budget_accounting():
    """Verify that search endpoint acquires tokens against the dedicated search budget (4 req/min)."""
    limiter = OFFRateLimiter(global_product_limit=10, global_search_limit=4, web_concurrency=1, render_instance_count=1)
    client = OpenFoodFactsClient(rate_limiter=limiter)

    # Search budget is 4 tokens per minute
    assert limiter.acquire("search") is True
    assert limiter.acquire("search") is True
    assert limiter.acquire("search") is True
    assert limiter.acquire("search") is True
    # 5th attempt must be rejected
    assert limiter.acquire("search") is False

    # Product budget (10) must remain unaffected by search acquisitions
    assert limiter.acquire("product") is True
