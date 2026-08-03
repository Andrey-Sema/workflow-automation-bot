"""Regression tests for graceful-shutdown behavior: aiogram's
start_polling() stops fetching new updates on SIGTERM but does not wait
for already-dispatched handler tasks to finish (verified by reading
Dispatcher._polling — each update is tracked only in a fire-and-forget
set). _wait_for_inflight_onec_entry() is what actually protects an
in-progress наряд from being cut off mid-typing by a container stop.

Each test gets its own fresh asyncio.Lock via monkeypatch rather than
sharing the real module-level singleton: asyncio.Lock binds to whichever
event loop first uses it (fine for the bot, which owns one loop for its
whole process lifetime via asyncio.run()) but pytest-asyncio gives each
test function a fresh loop, so a shared Lock object would only work in
the first test that touched it and raise "bound to a different event
loop" in every test after that - confirmed by hitting exactly that error
before adding this fixture.
"""

import asyncio

import pytest

import src.telegram_bot.bot as bot_module
from src.telegram_bot.bot import _wait_for_inflight_onec_entry


@pytest.fixture
def fresh_lock(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(bot_module, "onec_entry_lock", lock)
    return lock


async def test_returns_immediately_when_lock_free(fresh_lock):
    await asyncio.wait_for(_wait_for_inflight_onec_entry(timeout=5), timeout=1)


async def test_waits_for_lock_release_within_timeout(fresh_lock, caplog):
    await fresh_lock.acquire()

    async def release_soon():
        await asyncio.sleep(0.05)
        fresh_lock.release()

    release_task = asyncio.create_task(release_soon())
    with caplog.at_level("INFO"):
        await asyncio.wait_for(_wait_for_inflight_onec_entry(timeout=5), timeout=2)
    await release_task

    assert not fresh_lock.locked()
    assert any("завершён" in r.message for r in caplog.records)


async def test_gives_up_after_timeout_without_raising(fresh_lock, caplog):
    await fresh_lock.acquire()
    try:
        with caplog.at_level("CRITICAL"):
            # Must return (not raise) even though the lock is never released
            # in time - the container has to stop eventually regardless.
            await asyncio.wait_for(_wait_for_inflight_onec_entry(timeout=0.1), timeout=2)
        assert any("Не дождался" in r.message for r in caplog.records)
    finally:
        fresh_lock.release()


async def test_log_drain_task_is_cancellable():
    """Regression: the drain loop must never park a to_thread worker on a
    blocking queue.get() — task.cancel() can't reach such a thread, and
    asyncio.run()'s executor shutdown then joins it, hanging the whole
    process on SIGTERM until Docker SIGKILLs it. The threadless poll loop
    must cancel promptly instead."""
    from unittest.mock import AsyncMock

    from src.logging_setup import TelegramQueueHandler
    from src.telegram_bot.bot import _drain_log_queue_to_chat

    handler = TelegramQueueHandler()
    bot = AsyncMock()
    task = asyncio.create_task(_drain_log_queue_to_chat(bot, 123, handler))
    await asyncio.sleep(0.05)  # let it reach the empty-queue sleep

    task.cancel()
    # Must finish within the wait_for window - a stuck task would raise TimeoutError.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


async def test_log_drain_forwards_queued_records():
    from unittest.mock import AsyncMock

    from src.logging_setup import TelegramQueueHandler
    from src.telegram_bot.bot import _drain_log_queue_to_chat

    handler = TelegramQueueHandler()
    handler.queue.put_nowait(("ERROR", "boom happened"))
    handler.queue.put_nowait(("WARNING", "just a warning"))

    bot = AsyncMock()
    task = asyncio.create_task(_drain_log_queue_to_chat(bot, 123, handler))
    for _ in range(50):
        if bot.send_message.await_count >= 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()

    sent = [call.args for call in bot.send_message.await_args_list]
    assert sent[0] == (123, "🚨 boom happened")
    assert sent[1] == (123, "⚠️ just a warning")
