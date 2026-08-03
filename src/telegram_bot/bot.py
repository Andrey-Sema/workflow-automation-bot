"""Wires the Telegram bot together: settings, logging (incl. forwarding
WARNING+ log records to an admin chat), storage, middlewares and routers.
Entry point is `python -m src.telegram_bot` (see `__main__.py`).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from src.logging_setup import TelegramQueueHandler, setup_logging
from src.operators_store import OperatorsStore
from src.order_store import OrderStore
from src.preflight import run_preflight
from src.settings import get_settings, require_telegram_token
from src.telegram_bot.handlers import admin, common, orders
from src.telegram_bot.locks import onec_entry_lock
from src.telegram_bot.metrics_server import gauge_refresh_loop, start_metrics_server
from src.telegram_bot.middlewares import (
    AccessControlMiddleware,
    CorrelationIdMiddleware,
    ErrorHandlingMiddleware,
    ThrottlingMiddleware,
)
from src.telegram_bot.sqlite_storage import SQLiteStorage

logger = logging.getLogger(__name__)

HEARTBEAT_PATH_NAME = ".health"


async def _wait_for_inflight_onec_entry(timeout: float) -> None:
    """aiogram's start_polling() stops fetching new updates on SIGTERM/
    SIGINT but does NOT wait for already-dispatched update handlers to
    finish (confirmed by reading Dispatcher._polling: each update is an
    independent asyncio.create_task, never joined on shutdown). Without
    this, a container stop mid-1C-entry could cut off typing halfway
    through a наряд with no record of what actually got entered.

    Bounded wait: if nothing finishes in time we still have to let the
    container stop eventually, but we log as loudly as possible first.
    """
    if not onec_entry_lock.locked():
        return
    logger.warning(f"⏳ Дожидаюсь завершения текущего ввода в 1С перед остановкой (до {timeout:.0f}с)...")
    try:
        async with asyncio.timeout(timeout), onec_entry_lock:
            pass
        logger.info("✅ Ввод в 1С завершён, продолжаю остановку.")
    except TimeoutError:
        logger.critical(
            f"🚨 Не дождался завершения ввода в 1С за {timeout:.0f}с — "
            "контейнер всё равно остановится. ПРОВЕРЬ ОКНО 1С ВРУЧНУЮ перед следующим запуском!"
        )


async def _drain_log_queue_to_chat(bot: Bot, chat_id: int, handler: TelegramQueueHandler) -> None:
    """Background task: forwards WARNING+ log records to an admin chat.
    Never lets a broken Telegram API call take down logging — errors here
    are swallowed after one retry-free attempt.

    Polls with get_nowait()+sleep instead of parking a to_thread worker on
    a blocking queue.get(): task.cancel() can't reach a thread stuck in
    queue.get(), and asyncio.run()'s executor shutdown then joins that
    thread — hanging the whole process on SIGTERM until Docker SIGKILLs it.
    """
    while True:
        try:
            level, text = handler.queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.5)
            continue
        try:
            prefix = "🚨" if level in ("ERROR", "CRITICAL") else "⚠️"
            await bot.send_message(chat_id, f"{prefix} {text[:3900]}")
        except Exception:
            logger.debug("Failed to forward log record to admin chat", exc_info=True)


async def _heartbeat_loop(path) -> None:
    while True:
        try:
            path.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            # Transient (disk full, volume remount): keep looping. If it
            # keeps failing the file goes stale and Docker's healthcheck
            # restarts us anyway - which is the right escalation, unlike a
            # silently dead heartbeat task after a single blip.
            logger.warning("⚠️ Не удалось записать heartbeat-файл", exc_info=True)
        await asyncio.sleep(30)


def build_dispatcher(order_store: OrderStore, operators_store: OperatorsStore, storage: SQLiteStorage) -> Dispatcher:
    settings = get_settings()
    dp = Dispatcher(storage=storage)

    dp["settings"] = settings
    dp["order_store"] = order_store
    dp["operators_store"] = operators_store

    access_control = AccessControlMiddleware(settings, operators_store)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(CorrelationIdMiddleware())
        observer.outer_middleware(access_control)
        observer.outer_middleware(ErrorHandlingMiddleware())
    # Callback-only, NOT on dp.message: Telegram delivers a multi-photo
    # album as several separate Message updates milliseconds apart, and a
    # per-chat throttle on messages would silently drop every photo after
    # the first. Button mashing (the thing worth throttling — duplicate
    # Gemini runs, double 1C entries) only ever arrives as CallbackQuery.
    dp.callback_query.outer_middleware(ThrottlingMiddleware(settings.telegram_throttle_seconds))

    dp.include_router(orders.router)
    dp.include_router(admin.router)
    dp.include_router(common.router)  # last: has the catch-all fallback handler
    return dp


async def run() -> None:
    settings = get_settings()
    settings.ensure_directories()

    telegram_handler = None
    if settings.telegram_log_chat_id:
        telegram_handler = TelegramQueueHandler()
    setup_logging(
        settings.log_dir, settings.log_level, json_output=settings.log_json, telegram_handler=telegram_handler
    )

    token = require_telegram_token(settings)
    bot = Bot(token=token.get_secret_value(), default=DefaultBotProperties())

    await run_preflight(settings, bot)

    order_store = OrderStore(settings.orders_db_path)
    await order_store.init()
    operators_store = OperatorsStore(settings.operators_path)
    fsm_storage = SQLiteStorage(settings.fsm_storage_path)
    await fsm_storage.init()

    dp = build_dispatcher(order_store, operators_store, fsm_storage)

    background_tasks = [
        asyncio.create_task(_heartbeat_loop(settings.data_dir / HEARTBEAT_PATH_NAME)),
    ]
    if telegram_handler is not None:
        background_tasks.append(
            asyncio.create_task(_drain_log_queue_to_chat(bot, settings.telegram_log_chat_id, telegram_handler))
        )

    metrics_runner = None
    if settings.metrics_enabled:
        metrics_runner = await start_metrics_server(settings.metrics_port)
        background_tasks.append(asyncio.create_task(gauge_refresh_loop(settings, order_store)))

    logger.info("🤖 Workflow Automation Bot: запуск long-polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await _wait_for_inflight_onec_entry(settings.shutdown_grace_seconds)
        for task in background_tasks:
            task.cancel()
        if metrics_runner is not None:
            await metrics_runner.cleanup()
        await bot.session.close()
        await fsm_storage.close()
