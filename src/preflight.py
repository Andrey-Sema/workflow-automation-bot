"""Startup preflight checks: fail fast with one clear, actionable log
message instead of the bot starting up "successfully" and then failing
confusingly (or silently doing nothing useful) on the first real order.
"""

from __future__ import annotations

import logging

from aiogram import Bot

from src import config
from src.settings import Settings, require_gemini_api_key

logger = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Raised for a check that should stop the bot from starting at all."""


def check_gemini_key(settings: Settings) -> None:
    require_gemini_api_key(settings)


def check_data_dir_writable(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.data_dir / ".preflight_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise PreflightError(
            f"data/ ({settings.data_dir}) недоступна для записи: {e}. Проверь права на хосте — "
            "контейнер работает под фиксированным UID 1000 (см. README 'Первый запуск')."
        ) from e


def check_catalog_loaded(settings: Settings) -> str | None:
    """Non-fatal by design: returns a warning string instead of raising.
    The catalog can be dropped in and picked up with /reload_catalog after
    the bot is already running — no reason to block startup on it.
    """
    if not config.catalog_is_loaded():
        return (
            f"⚠️ {settings.catalog_path} не найден или пуст — бизнес-логика (тарифы, "
            "сопоставление с 1С) не будет работать, пока файла нет. Как появится — /reload_catalog."
        )

    # Loaded is not the same as sound. Ambiguous mappings never raise; they
    # just make the bot type the wrong 1C line, so say so at boot while
    # someone is still looking at the logs.
    report = config.check_catalog()
    if not report.ok:
        return (
            f"⚠️ В каталоге {len(report.errors)} ошибок структуры — часть позиций не будет "
            "сопоставляться с 1С. Подробности: /catalog_check"
        )
    if report.warnings:
        return (
            f"⚠️ В каталоге {len(report.warnings)} неоднозначностей (одинаковые цены, "
            "конфликты dropdown_index). Подробности: /catalog_check"
        )
    return None


async def check_telegram_token(bot: Bot) -> None:
    try:
        me = await bot.get_me()
    except Exception as e:
        raise PreflightError(f"Не удалось подключиться к Telegram с этим TELEGRAM_BOT_TOKEN: {e}") from e
    logger.info(f"✅ Telegram OK: @{me.username}")


async def run_preflight(settings: Settings, bot: Bot) -> None:
    """Raises PreflightError (or the RuntimeError from require_gemini_api_key)
    on anything serious enough to justify not starting at all."""
    logger.info("🔎 Preflight-проверки перед стартом...")

    check_gemini_key(settings)
    check_data_dir_writable(settings)
    await check_telegram_token(bot)

    catalog_warning = check_catalog_loaded(settings)
    if catalog_warning:
        logger.warning(catalog_warning)

    logger.info(f"⌨️ Режим ввода в 1С: {'🧪 dry-run' if settings.onec_dry_run else '🔴 БОЕВОЙ режим'}")
    logger.info("✅ Preflight пройден, бот стартует.")
