import asyncio
import contextlib

from aiohttp.test_utils import make_mocked_request

from src import metrics
from src.metrics import ORDERS_PENDING, RDP_CONNECTED
from src.order_conflicts import build_order_summary
from src.order_store import ENTERED, PENDING, OrderStore
from src.settings import Settings
from src.telegram_bot.metrics_server import _metrics_handler, gauge_refresh_loop, start_metrics_server


async def test_metrics_handler_returns_prometheus_text_format():
    metrics.PIPELINE_RUNS.labels(outcome="success").inc()  # ensure at least one sample exists

    request = make_mocked_request("GET", "/metrics")
    response = await _metrics_handler(request)

    assert response.status == 200
    assert "text/plain" in response.content_type
    body = response.body.decode("utf-8")
    assert "workflow_pipeline_runs_total" in body


async def test_start_metrics_server_serves_real_http():
    runner = await start_metrics_server(port=19191)
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session, session.get("http://127.0.0.1:19191/metrics") as resp:
            assert resp.status == 200
            text = await resp.text()
            assert "workflow_" in text
    finally:
        await runner.cleanup()


async def test_gauge_refresh_loop_updates_rdp_gauge(tmp_path):
    (tmp_path / ".rdp_status").write_text("connected", encoding="utf-8")
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)
    store = OrderStore(tmp_path / "orders.db")
    await store.init()

    task = asyncio.create_task(gauge_refresh_loop(settings, store, interval=100))
    await asyncio.sleep(0.05)  # let the first iteration run before the long sleep
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert RDP_CONNECTED._value.get() == 1.0


async def test_gauge_refresh_loop_counts_only_pending_orders(tmp_path):
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)
    store = OrderStore(tmp_path / "orders.db")
    await store.init()

    summary = build_order_summary({"deceased": {"fio": "Тест"}})
    await store.create_pending("ORD1", 1, [], 0, {}, summary)
    await store.create_pending("ORD2", 1, [], 0, {}, summary)
    await store.set_status("ORD2", ENTERED)

    task = asyncio.create_task(gauge_refresh_loop(settings, store, interval=100))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert ORDERS_PENDING._value.get() == 1.0
    remaining_pending = [r for r in await store.recent(limit=10) if r.status == PENDING]
    assert len(remaining_pending) == 1
