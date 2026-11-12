import asyncio
import threading
import pytest
import httpx
from fastapi.testclient import TestClient

from api.main import app, lifespan
from services.off_client import OpenFoodFactsClient, get_default_off_client, reset_default_off_client


@pytest.mark.anyio
async def test_lifespan_binds_and_unbinds_async_client():
    """Verify that FastAPI lifespan correctly binds and unbinds httpx.AsyncClient."""
    off_client = get_default_off_client()
    assert off_client.get_async_client() is None

    async with lifespan(app):
        bound = off_client.get_async_client()
        assert bound is not None
        assert isinstance(bound, httpx.AsyncClient)
        assert not bound.is_closed

    # After lifespan exit, client must be unbound
    assert off_client.get_async_client() is None


@pytest.mark.anyio
async def test_off_client_reuses_bound_client():
    """Verify that get_async_client returns the exact bound instance across multiple calls."""
    client = OpenFoodFactsClient()
    assert client.get_async_client() is None

    async with httpx.AsyncClient() as shared_http:
        client.bind_lifespan_client(shared_http)
        c1 = client.get_async_client()
        c2 = client.get_async_client()
        assert c1 is shared_http
        assert c2 is shared_http
        assert c1 is c2

        client.unbind_lifespan_client()
        assert client.get_async_client() is None


@pytest.mark.anyio
async def test_off_client_fallback_when_unbound():
    """Verify that _execute_http_async falls back safely when no lifespan client is bound."""
    client = OpenFoodFactsClient()
    assert client.get_async_client() is None

    # An invalid barcode fails before network, but let's test execute directly or with mock
    resp, attempts, err = await client._execute_http_async(
        url="http://127.0.0.1:1/nonexistent",
        timeout=0.1,
    )
    # Should safely fail without crashing due to missing client
    assert attempts >= 1
    assert err is not None


def test_cross_loop_safety_detection():
    """Verify that get_async_client rejects cross-loop access while the client remains open."""
    client = OpenFoodFactsClient()

    step1_bound = threading.Event()
    step2_checked = threading.Event()
    thread_errors = []

    def thread1_worker():
        async def _loop1_runner():
            shared = httpx.AsyncClient()
            try:
                # 1. Bind shared client on Loop 1
                client.bind_lifespan_client(shared)
                # Verify that Loop 1 can retrieve the client
                if client.get_async_client() is not shared:
                    thread_errors.append("Loop 1 failed to retrieve bound client")
                if shared.is_closed:
                    thread_errors.append("Client was prematurely closed on Loop 1")

                # Signal Loop 2 that client is bound and open
                step1_bound.set()

                # Wait for Loop 2 to attempt cross-loop retrieval
                if not step2_checked.wait(timeout=5.0):
                    thread_errors.append("Loop 1 timed out waiting for Loop 2 verification")

                # 4. Verify Loop 1 can still retrieve the client
                if client.get_async_client() is not shared:
                    thread_errors.append("Loop 1 failed to re-retrieve client after Loop 2 check")
            finally:
                # 5. Safe cleanup on Loop 1
                await shared.aclose()
                client.unbind_lifespan_client()

        asyncio.run(_loop1_runner())

    t1 = threading.Thread(target=thread1_worker, daemon=True)
    t1.start()

    try:
        # Wait for Loop 1 to bind the client
        assert step1_bound.wait(timeout=5.0), "Timed out waiting for Loop 1 to bind client"

        async def _loop2_runner():
            # 2. Client is confirmed open
            raw_client = client._async_client
            assert raw_client is not None, "Client should be bound"
            assert not raw_client.is_closed, "Client must be open during cross-loop test"

            # 3. Verify Loop 2 CANNOT retrieve the client
            retrieved = client.get_async_client()
            assert retrieved is None, f"Cross-loop access should return None, got {retrieved}"

        # Run Loop 2 in an independent event loop
        asyncio.run(_loop2_runner())
    finally:
        # Signal Loop 1 to continue and clean up
        step2_checked.set()
        t1.join(timeout=5.0)

    # Ensure no errors occurred in thread 1
    assert not thread_errors, f"Thread 1 encountered errors: {thread_errors}"
    assert client.get_async_client() is None, "Client should be completely unbound after test"
