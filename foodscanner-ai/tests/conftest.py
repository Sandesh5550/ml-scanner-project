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


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Reset process-local rate limiter before and after each test for clean isolation."""
    limiter = get_rate_limiter()
    limiter.reset()
    yield
    limiter.reset()
