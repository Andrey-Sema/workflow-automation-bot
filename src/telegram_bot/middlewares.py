"""Cross-cutting concerns for every incoming Message/CallbackQuery:
correlation ids for logging, an operator/admin allowlist, and turning
exceptions into friendly chat replies instead of silent failures or raw
tracebacks.

The actual decision logic (`extract_chat_id`, `check_access`) is kept as
plain functions so it can be unit-tested without spinning up a live
aiogram Dispatcher/Bot.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.errors import AccessDeniedError, WorkflowError
from src.logging_setup import set_correlation_id
from src.operators_store import OperatorsStore
from src.settings import Settings

logger = logging.getLogger(__name__)


def extract_chat_id(event: TelegramObject) -> int | None:
    """Works for both Message and CallbackQuery."""
    chat = getattr(event, "chat", None)
    if chat is not None:
        return chat.id
    message = getattr(event, "message", None)
    if message is not None and getattr(message, "chat", None) is not None:
        return message.chat.id
    return None


def check_access(chat_id: int | None, settings: Settings, operators_store: OperatorsStore) -> bool:
    if chat_id is None:
        return False
    return settings.is_operator(chat_id) or chat_id in operators_store.list()


class CorrelationIdMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        set_correlation_id(uuid.uuid4().hex[:8])
        return await handler(event, data)


class AccessControlMiddleware(BaseMiddleware):
    """Restricts the bot to known operators/admins. Everyone else gets one
    polite refusal; no handler runs, no internal state is touched."""

    def __init__(self, settings: Settings, operators_store: OperatorsStore) -> None:
        self.settings = settings
        self.operators_store = operators_store

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        chat_id = extract_chat_id(event)
        allowed = check_access(chat_id, self.settings, self.operators_store)
        data["is_admin"] = chat_id is not None and self.settings.is_admin(chat_id)
        data["is_operator"] = allowed

        if not allowed:
            logger.warning(f"⛔ Доступ отклонён для chat_id={chat_id}")
            if hasattr(event, "answer"):
                await event.answer(AccessDeniedError().user_message)
            return None

        return await handler(event, data)


class ErrorHandlingMiddleware(BaseMiddleware):
    """Turns a `WorkflowError` into its friendly `.user_message` in chat;
    logs anything else with a full traceback and shows a generic message —
    the user never sees a raw stack trace."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        try:
            return await handler(event, data)
        except WorkflowError as e:
            logger.warning(f"⚠️ {type(e).__name__}: {e}")
            await _reply(event, e.user_message)
        except Exception:
            logger.exception("❌ Необработанная ошибка в обработчике Telegram")
            await _reply(event, "💥 Внутренняя ошибка. Подробности — в логах, администратор уже уведомлён.")
        return None


async def _reply(event: TelegramObject, text: str) -> None:
    message = getattr(event, "message", None)
    target = message if message is not None else event
    if hasattr(target, "answer"):
        await target.answer(text)
