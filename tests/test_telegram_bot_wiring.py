"""Smoke tests for bot wiring: no live Telegram connection needed, this
just verifies the Dispatcher assembles without import/registration errors
and that the expected routers + shared dependencies are present."""

from src.operators_store import OperatorsStore
from src.order_store import OrderStore
from src.telegram_bot.bot import build_dispatcher
from src.telegram_bot.middlewares import ThrottlingMiddleware
from src.telegram_bot.sqlite_storage import SQLiteStorage


def test_build_dispatcher_wiring(tmp_path):
    # aiogram routers are module-level singletons that can only ever be
    # attached to one Dispatcher, so this stays a single test (build once).
    order_store = OrderStore(tmp_path / "orders.db")
    operators_store = OperatorsStore(tmp_path / "operators.json")
    fsm_storage = SQLiteStorage(tmp_path / "fsm.db")

    dp = build_dispatcher(order_store, operators_store, fsm_storage)

    router_names = {router.name for router in dp.sub_routers}
    assert router_names == {"orders", "admin", "common"}
    # common must be included last: it has the catch-all `@router.message()`
    assert dp.sub_routers[-1].name == "common"

    assert dp["order_store"] is order_store
    assert dp["operators_store"] is operators_store
    assert dp["settings"] is not None

    # Regression: throttling must NOT be on dp.message — Telegram delivers a
    # multi-photo album as several Message updates milliseconds apart, and a
    # per-chat message throttle silently drops every photo after the first.
    message_mw = [type(m) for m in dp.message.outer_middleware]
    callback_mw = [type(m) for m in dp.callback_query.outer_middleware]
    assert ThrottlingMiddleware not in message_mw
    assert ThrottlingMiddleware in callback_mw
