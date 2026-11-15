import os
import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# Limit OpenBLAS and multi-threading allocation to prevent heap exhaustion on constrained Windows environments
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import pytest
from services.rate_limiter import get_rate_limiter
from services.off_client import reset_default_off_client
from services.product_data_service import reset_default_product_data_service


@pytest.fixture(autouse=True)
def reset_rate_limits(monkeypatch):
    """Isolate unit tests from shared Redis counters and reset process-local state.

    Tests for Redis-backed behavior must opt in explicitly and use isolated keys.
    The ordinary suite exercises deterministic process-local sliding-window behavior.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    limiter = get_rate_limiter()
    limiter.reset()
    reset_default_off_client()
    reset_default_product_data_service()
    yield
    limiter.reset()
    reset_default_off_client()
    reset_default_product_data_service()
