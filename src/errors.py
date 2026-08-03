"""Domain exception hierarchy.

Every exception carries a `user_message` — a short, non-technical Russian
sentence safe to forward straight into a Telegram chat. Technical detail
stays in `str(exc)` / logs only, so operators never see stack traces but the
log file always has the full picture.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all expected, user-facing failures in the pipeline."""

    default_message = "Произошла ошибка. Подробности — в логах."

    def __init__(self, detail: str = "", *, user_message: str | None = None) -> None:
        self.user_message = user_message or self.default_message
        super().__init__(detail or self.user_message)


class CatalogLoadError(WorkflowError):
    default_message = "⚠️ Не удалось загрузить справочник услуг (catalog.json). Обратись к администратору."


class GeminiParsingError(WorkflowError):
    default_message = "🧠 Нейросеть не смогла распознать бланк. Попробуй переснять фото при хорошем освещении."


class ValidationFailedError(WorkflowError):
    default_message = "🛡️ Данные не прошли проверку структуры. Наряд нужно проверить вручную."


class FileIntakeError(WorkflowError):
    default_message = "📎 Файл не принят: неверный формат или превышен размер."


class AccessDeniedError(WorkflowError):
    default_message = "⛔ У тебя нет доступа к этой функции бота."


class OrderNotFoundError(WorkflowError):
    default_message = "🔍 Наряд не найден (возможно, уже обработан или отменён)."


class RdpConnectionError(WorkflowError):
    default_message = "🖥️ Нет связи с удалённым рабочим столом (RDP). 1С недоступна."


class OneCInputError(WorkflowError):
    default_message = "⌨️ Не удалось ввести данные в 1С. Экран мог измениться — проверь вручную."
