"""Tiny aiohttp server exposing Prometheus metrics on `/metrics`. Runs
alongside aiogram's polling loop in the same asyncio event loop — polling
doesn't use an HTTP server itself, so this doesn't conflict with anything.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.circuit_breaker import gemini_circuit_breaker
from src.metrics import GEMINI_CIRCUIT_BREAKER_OPEN, ORDERS_PENDING, RDP_CONNECTED
from src.order_store import PENDING, OrderStore
from src.rdp_status import is_rdp_connected
from src.settings import Settings

logger = logging.getLogger(__name__)


async def _metrics_handler(request: web.Request) -> web.Response:
    # CONTENT_TYPE_LATEST already includes "; charset=utf-8", and aiohttp's
    # content_type= kwarg rejects a charset embedded in it (raises
    # ValueError) - it wants charset passed separately. Setting the raw
    # header instead sidesteps that parsing entirely.
    return web.Response(body=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


async def start_metrics_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # Binding all interfaces is intentional: scraped by the `prometheus`
    # service over the docker-compose network, not exposed publicly (the
    # port isn't published to the host in docker-compose.yml by default).
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104  # nosec B104
    await site.start()
    logger.info(f"📊 Метрики Prometheus на порту {port}/metrics")
    return runner


async def gauge_refresh_loop(settings: Settings, order_store: OrderStore, interval: float = 15.0) -> None:
    """Keeps the point-in-time gauges (RDP connectivity, circuit breaker
    state, pending-order count) fresh — unlike counters/histograms these
    represent current state, not something naturally incremented at the
    point it happens.
    """
    while True:
        # One bad iteration (disk hiccup, transient sqlite error) must not
        # kill the loop for the rest of the process lifetime - stale-forever
        # gauges lie worse than a briefly-skipped refresh.
        try:
            RDP_CONNECTED.set(1 if is_rdp_connected(settings) else 0)
            GEMINI_CIRCUIT_BREAKER_OPEN.set(1 if gemini_circuit_breaker.is_open else 0)
            pending = await order_store.recent(limit=1000)
            ORDERS_PENDING.set(sum(1 for r in pending if r.status == PENDING))
        except Exception:
            logger.debug("Failed to refresh gauges", exc_info=True)
        await asyncio.sleep(interval)
