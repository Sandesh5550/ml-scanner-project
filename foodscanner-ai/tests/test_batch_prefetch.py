import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from database.db_session import SessionLocal
from database.models import Product, Nutrition
from schemas.batch import BatchActionTaken, BatchItemStatus
from schemas.product import CanonicalProduct
from services.batch_ingestion_service import BatchIngestionService
from services.product_data_service import get_default_product_data_service


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.mark.anyio
async def test_batch_prefetch_chunks_at_250():
    """Verify that prefetch_existing_canonical_products splits large barcode lists into 250-item chunks."""
    service = BatchIngestionService()
    # 600 barcodes
    barcodes = [f"890100000{i:03d}" for i in range(600)]

    chunk_sizes = []
    real_session_factory = service.session_factory

    def recording_session_factory():
        sess = real_session_factory()
        original_execute = sess.execute

        def recording_execute(statement, *args, **kwargs):
            # Check if this is the bulk query
            stmt_str = str(statement)
            if "WHERE products.barcode IN" in stmt_str or "products.barcode" in stmt_str:
                pass
            return original_execute(statement, *args, **kwargs)

        sess.execute = recording_execute
        return sess

    # Test prefetch directly with small chunk_size to verify chunking logic
    with patch("services.db_service.get_products_by_barcodes_bulk", return_value=[]) as mock_bulk:
        service.prefetch_existing_canonical_products(barcodes, chunk_size=250)
        # 600 items chunked into 250 should yield 3 calls: 250, 250, 100
        assert mock_bulk.call_count == 3
        calls = [len(c.args[1]) for c in mock_bulk.call_args_list]
        assert calls == [250, 250, 100]


@pytest.mark.anyio
async def test_prefetched_cached_fresh_skips_per_item_queries():
    """Verify that cached fresh products resolve via prefetched cache with zero per-item network or DB queries."""
    service = BatchIngestionService()
    fake_barcode = "8901234567891"

    fake_canonical = CanonicalProduct(
        canonical_id=fake_barcode,
        barcode=fake_barcode,
        product_name="Prefetched Biscuit",
        brand="Britannia",
    )

    with patch.object(service, "prefetch_existing_canonical_products", return_value={fake_barcode: fake_canonical}):
        with patch.object(service.product_data_service, "is_fresh", return_value=True):
            with patch.object(service.product_data_service, "get_product_by_barcode") as mock_get_single:
                result = await service.ingest_batch_async([fake_barcode])
                assert result.successfully_processed == 1
                assert result.items[0].action_taken == BatchActionTaken.CACHED_FRESH
                assert result.items[0].product_name == "Prefetched Biscuit"
                # Must NOT call the per-item get_product_by_barcode method!
                assert not mock_get_single.called


@pytest.mark.anyio
async def test_force_refresh_bypasses_prefetch_cache():
    """Verify that force_refresh=True does not use prefetched cached fresh shortcut."""
    service = BatchIngestionService()
    fake_barcode = "8901234567892"

    fake_canonical = CanonicalProduct(
        canonical_id=fake_barcode,
        barcode=fake_barcode,
        product_name="Prefetched Biscuit",
        brand="Britannia",
    )

    with patch.object(service, "prefetch_existing_canonical_products", return_value={fake_barcode: fake_canonical}) as mock_prefetch:
        with patch.object(service.product_data_service, "get_product_by_barcode_async", return_value=fake_canonical) as mock_async_get:
            result = await service.ingest_batch_async([fake_barcode], force_refresh=True)
            # When force_refresh=True, prefetch is skipped
            assert not mock_prefetch.called
            assert mock_async_get.called
            assert result.items[0].action_taken == BatchActionTaken.FETCHED
