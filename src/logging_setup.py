"""Structured logging: correlation ids, rotating file handler, optional JSON
output, and a queue-based handler that lets the Telegram bot forward
WARNING+ records to an admin chat without logging calls ever blocking on
network I/O.
"""

from __future__ import annotations

import json
import logging
import queue
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_CONSOLE_FMT = "%(asctime)s | %(levelname)-8s | %(correlation_id)s | %(name)s | %(message)s"


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get()


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TelegramQueueHandler(logging.Handler):
    """Puts formatted (level, text) tuples on a thread-safe queue.

    Decoupled from aiogram on purpose: the bot's background task owns the
    queue and does the actual network send, so a slow/broken Telegram API
    call can never block application logging.
    """

    def __init__(self, level: int = logging.WARNING, maxsize: int = 200) -> None:
        super().__init__(level=level)
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=maxsize)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            self.queue.put_nowait((record.levelname, text))
        except queue.Full:
            pass
        except Exception:
            self.handleError(record)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    *,
    json_output: bool = False,
    telegram_handler: TelegramQueueHandler | None = None,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    correlation_filter = _CorrelationFilter()
    formatter: logging.Formatter = _JsonFormatter() if json_output else logging.Formatter(
        _CONSOLE_FMT, datefmt="%H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(correlation_filter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.addFilter(correlation_filter)
    root.addHandler(file_handler)

    if telegram_handler is not None:
        telegram_handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
        telegram_handler.addFilter(correlation_filter)
        root.addHandler(telegram_handler)

    # Quiet down noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "google_genai.models", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
