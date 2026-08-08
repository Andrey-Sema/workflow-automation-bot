"""Tests for logging setup, with emphasis on secret redaction.

Redaction matters more here than in a typical service: `TelegramQueueHandler`
forwards WARNING+ records into an admin Telegram chat, so a secret in a log
line leaves the machine and lands in a third-party service that keeps it.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.logging_setup import (
    TelegramQueueHandler,
    _JsonFormatter,
    get_correlation_id,
    redact_secrets,
    set_correlation_id,
    setup_logging,
)

BOT_TOKEN = "123456789:AAFAKEfakeFAKEtokenTESTONLY_abcdefghij"
GEMINI_KEY = "AIzaSyFAKEfakeFAKEkeyTESTONLY_abcdefgh"


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)


# --- redact_secrets ---

@pytest.mark.parametrize("text, secret", [
    (f"Не удалось подключиться: https://api.telegram.org/bot{BOT_TOKEN}/getMe", BOT_TOKEN),
    (f"GEMINI_API_KEY={GEMINI_KEY}", GEMINI_KEY),
    (f'{{"api_key": "{GEMINI_KEY}"}}', GEMINI_KEY),
    ("RDP_PASSWORD=hunter2supersecret", "hunter2supersecret"),
    ("token: abcd1234efgh5678", "abcd1234efgh5678"),
])
def test_secret_shaped_text_is_masked(text, secret):
    cleaned = redact_secrets(text)
    assert secret not in cleaned
    assert "REDACTED" in cleaned


@pytest.mark.parametrize("text", [
    "",
    "Наряд #A1B2C3D4 внесён в 1С (12 позиций)",
    "Труна Економ 2700 грн",
    "⚠️ Каталог загружен с замечаниями: 0 ошибок, 10 предупреждений",
])
def test_ordinary_log_lines_are_left_alone(text):
    assert redact_secrets(text) == text


def test_redaction_keeps_the_key_name_so_the_line_stays_readable():
    """Смысл строки должен сохраниться: видно, ЧТО не задано, но не значение."""
    cleaned = redact_secrets(f"GEMINI_API_KEY={GEMINI_KEY}")
    assert cleaned.startswith("GEMINI_API_KEY=")


def test_redaction_never_raises(tmp_path):
    for weird in ["\x00\x01", "％" * 100, "token=", ":" * 50]:
        assert isinstance(redact_secrets(weird), str)


# --- интеграция с handler'ами ---

def test_secret_is_masked_in_the_file_log(tmp_path):
    setup_logging(tmp_path, level="INFO")
    logging.getLogger("test").warning("upstream said https://api.telegram.org/bot%s/getMe", BOT_TOKEN)
    for h in logging.getLogger().handlers:
        h.flush()

    written = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert BOT_TOKEN not in written
    assert "REDACTED" in written


def test_secret_is_masked_before_reaching_the_admin_chat(tmp_path):
    """Самый важный путь: очередь TelegramQueueHandler уходит наружу."""
    handler = TelegramQueueHandler(level=logging.WARNING)
    setup_logging(tmp_path, level="INFO", telegram_handler=handler)

    logging.getLogger("test").error(f"config dump: GEMINI_API_KEY={GEMINI_KEY}")

    _level, text = handler.queue.get_nowait()
    assert GEMINI_KEY not in text
    assert "REDACTED" in text


def test_lazy_percent_args_are_still_rendered_after_redaction(tmp_path):
    """Фильтр подменяет record.msg и очищает args — форматирование не должно
    сломаться и потерять подставленные значения."""
    setup_logging(tmp_path, level="INFO")
    logging.getLogger("test").warning("наряд %s, позиций %d", "A1B2C3D4", 12)
    for h in logging.getLogger().handlers:
        h.flush()

    written = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "A1B2C3D4" in written
    assert "позиций 12" in written


# --- correlation id ---

def test_correlation_id_round_trips():
    set_correlation_id("abc123")
    assert get_correlation_id() == "abc123"


def test_correlation_id_reaches_the_file_log(tmp_path):
    setup_logging(tmp_path, level="INFO")
    set_correlation_id("deadbeef")
    logging.getLogger("test").info("привет")
    for h in logging.getLogger().handlers:
        h.flush()

    line = json.loads((tmp_path / "app.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["correlation_id"] == "deadbeef"
    assert line["message"] == "привет"


# --- форматирование ---

def test_json_formatter_includes_traceback_on_exception():
    try:
        raise ValueError("бум")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "упало", None, True)
        import sys
        record.exc_info = sys.exc_info()

    payload = json.loads(_JsonFormatter().format(record))
    assert payload["level"] == "ERROR"
    assert "ValueError: бум" in payload["exc_info"]


def test_json_output_mode_writes_json_to_console(tmp_path, capsys):
    setup_logging(tmp_path, level="INFO", json_output=True)
    logging.getLogger("test").info("структурировано")
    captured = capsys.readouterr()
    assert json.loads(captured.err.strip().splitlines()[-1])["message"] == "структурировано"


# --- очередь для админ-чата ---

def test_queue_handler_drops_records_when_full_instead_of_blocking():
    """Переполненная очередь не должна вешать логирование — потерять
    предупреждение лучше, чем застопорить пайплайн."""
    handler = TelegramQueueHandler(level=logging.WARNING, maxsize=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for i in range(10):
        handler.emit(logging.LogRecord("t", logging.WARNING, __file__, 1, f"m{i}", None, None))

    assert handler.queue.qsize() == 2


def test_queue_handler_ignores_records_below_its_level(tmp_path):
    handler = TelegramQueueHandler(level=logging.WARNING)
    setup_logging(tmp_path, level="INFO", telegram_handler=handler)
    logging.getLogger("test").info("это не должно уйти в админ-чат")
    assert handler.queue.empty()


def test_setup_logging_replaces_handlers_instead_of_stacking(tmp_path):
    setup_logging(tmp_path, level="INFO")
    first = len(logging.getLogger().handlers)
    setup_logging(tmp_path, level="INFO")
    assert len(logging.getLogger().handlers) == first


def test_noisy_third_party_loggers_are_quieted(tmp_path):
    setup_logging(tmp_path, level="DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("aiogram.event").level == logging.WARNING
