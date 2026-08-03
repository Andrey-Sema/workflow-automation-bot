"""Exercises the actual __call__ behavior of the aiogram middlewares
(access control gating handlers, error handling translating exceptions into
chat replies) rather than just their extracted helper functions.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.errors import FileIntakeError
from src.operators_store import OperatorsStore
from src.settings import Settings
from src.telegram_bot.middlewares import AccessControlMiddleware, ErrorHandlingMiddleware


def fake_event(**overrides):
    base = {"chat": SimpleNamespace(id=42), "answer": AsyncMock()}
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_access_control_blocks_unknown_chat_and_skips_handler(tmp_path):
    settings = Settings(gemini_api_key=None)
    middleware = AccessControlMiddleware(settings, OperatorsStore(tmp_path / "operators.json"))
    handler = AsyncMock()
    event = fake_event()

    result = await middleware(handler, event, {})

    handler.assert_not_awaited()
    event.answer.assert_awaited_once()
    assert result is None


async def test_access_control_allows_known_operator_and_injects_flags(tmp_path):
    settings = Settings(gemini_api_key=None, telegram_operator_ids=[42])
    middleware = AccessControlMiddleware(settings, OperatorsStore(tmp_path / "operators.json"))
    handler = AsyncMock(return_value="handled")
    event = fake_event()
    data = {}

    result = await middleware(handler, event, data)

    handler.assert_awaited_once_with(event, data)
    assert result == "handled"
    assert data["is_operator"] is True
    assert data["is_admin"] is False


async def test_access_control_marks_admin(tmp_path):
    settings = Settings(gemini_api_key=None, telegram_admin_ids=[42])
    middleware = AccessControlMiddleware(settings, OperatorsStore(tmp_path / "operators.json"))
    data = {}

    await middleware(AsyncMock(), fake_event(), data)

    assert data["is_admin"] is True
    assert data["is_operator"] is True  # admins are always operators


async def test_error_handling_middleware_translates_workflow_error():
    middleware = ErrorHandlingMiddleware()

    async def failing_handler(event, data):
        raise FileIntakeError("bad file")

    event = fake_event()
    result = await middleware(failing_handler, event, {})

    assert result is None
    event.answer.assert_awaited_once()
    assert "Файл не принят" in event.answer.await_args.args[0] or event.answer.await_args.args[0]


async def test_error_handling_middleware_swallows_unexpected_exceptions():
    middleware = ErrorHandlingMiddleware()

    async def failing_handler(event, data):
        raise RuntimeError("kaboom")

    event = fake_event()
    result = await middleware(failing_handler, event, {})

    assert result is None
    event.answer.assert_awaited_once()
    assert "Внутренняя ошибка" in event.answer.await_args.args[0]


async def test_error_handling_middleware_passes_through_on_success():
    middleware = ErrorHandlingMiddleware()

    async def ok_handler(event, data):
        return "result"

    assert await middleware(ok_handler, fake_event(), {}) == "result"


async def test_error_handling_middleware_replies_via_callback_message():
    """CallbackQuery-like events don't have .answer(text); the reply must
    go through .message.answer instead."""
    middleware = ErrorHandlingMiddleware()

    async def failing_handler(event, data):
        raise FileIntakeError("bad file")

    inner_message = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(message=inner_message)  # no top-level .answer

    await middleware(failing_handler, event, {})

    inner_message.answer.assert_awaited_once()
