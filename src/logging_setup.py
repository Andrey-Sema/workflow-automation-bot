"""Structured logging: correlation ids, rotating file handler, optional JSON
output, and a queue-based handler that lets the Telegram bot forward
WARNING+ records to an admin chat without logging calls ever blocking on
network I/O.
"""

from __future__ import annotations

import json
import logging
import queue
import re
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_CONSOLE_FMT = "%(asctime)s | %(levelname)-8s | %(correlation_id)s | %(name)s | %(message)s"

# Log records don't only reach a file here: TelegramQueueHandler forwards
# WARNING+ into an admin chat, i.e. off the machine and into a third-party
# service that retains it. That makes "a secret ends up in a log line" an
# exfiltration path rather than a local hygiene problem, so secret-shaped
# text is scrubbed centrally instead of relying on every call site to be
# careful. aiogram is well-behaved today (it does not put the bot token in
# exception text — checked against TelegramNetworkError), and this exists so
# that stays true no matter what a future call site or library logs.
_REDACTED = "***REDACTED***"

# Telegram bot token: <digits>:<35-ish base64url chars>. No leading \b — the
# token usually appears mid-URL ("/bot123456789:AA..."), where the character
# before the digits is a letter and \b therefore never fires.
_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")

# Google/Gemini API key.
_APIKEY_RE = re.compile(r"AIza[A-Za-z0-9_-]{20,}")

# KEY=value / "password": "value" in a config dump or URL query. The key may
# carry a prefix ("RDP_PASSWORD", "TELEGRAM_BOT_TOKEN"), so the leading
# word characters are consumed rather than excluded by \b.
_KEYVALUE_RE = re.compile(
    r"(?i)([\w.-]*(?:api[_-]?key|token|password|passwd|secret))(\s*[=:]\s*)(\"?)([^\s,;\"'&]{4,})"
)


def redact_secrets(text: str) -> str:
    """Masks secret-shaped substrings. Best-effort by design: it must never
    raise or drop a log line, because a lost warning is worse than an
    imperfectly-masked one."""
    if not text:
        return text
    text = _TOKEN_RE.sub(_REDACTED, text)
    text = _APIKEY_RE.sub(_REDACTED, text)
    return _KEYVALUE_RE.sub(rf"\1\2\3{_REDACTED}", text)


class _RedactingFilter(logging.Filter):
    """Rewrites the record's message in place, before any handler formats it.

    Applied to the record (not to one formatter) so console, rotating file
    and the Telegram forwarder are all covered by the same pass.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            original = record.getMessage()
        except Exception:  # pragma: no cover - a broken %-format arg
            return True
        cleaned = redact_secrets(original)
        if cleaned != original:
            record.msg = cleaned
            record.args = ()
        return True


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
    redacting_filter = _RedactingFilter()
    formatter: logging.Formatter = _JsonFormatter() if json_output else logging.Formatter(
        _CONSOLE_FMT, datefmt="%H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(correlation_filter)
    console.addFilter(redacting_filter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.addFilter(correlation_filter)
    file_handler.addFilter(redacting_filter)
    root.addHandler(file_handler)

    if telegram_handler is not None:
        telegram_handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
        telegram_handler.addFilter(correlation_filter)
        telegram_handler.addFilter(redacting_filter)
        root.addHandler(telegram_handler)

    # Quiet down noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "google_genai.models", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
