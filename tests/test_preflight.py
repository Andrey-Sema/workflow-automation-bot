from unittest.mock import AsyncMock

import pytest

from src import config
from src.preflight import (
    PreflightError,
    check_catalog_loaded,
    check_data_dir_writable,
    check_gemini_key,
    check_telegram_token,
    run_preflight,
)
from src.settings import Settings


def test_check_gemini_key_passes_when_set(tmp_path):
    check_gemini_key(Settings(gemini_api_key="dummy", data_dir=tmp_path))


def test_check_gemini_key_raises_when_missing(tmp_path):
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        check_gemini_key(Settings(gemini_api_key=None, data_dir=tmp_path))


def test_check_data_dir_writable_creates_and_cleans_up_probe_file(tmp_path):
    target = tmp_path / "does" / "not" / "exist" / "yet"
    settings = Settings(gemini_api_key="dummy", data_dir=target)

    check_data_dir_writable(settings)

    assert target.is_dir()
    assert not (target / ".preflight_write_test").exists()


def test_check_data_dir_writable_raises_preflight_error_on_os_error(tmp_path, monkeypatch):
    """Mocked rather than a real chmod(0o500): this test suite runs as
    root in some environments (incl. this one), where the kernel simply
    doesn't enforce the write-permission bit against root, which would
    make a real permissions-based test silently no-op instead of catching
    a regression."""
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)

    def _raise(*a, **k):
        raise OSError("Read-only file system")

    monkeypatch.setattr("pathlib.Path.write_text", _raise)

    with pytest.raises(PreflightError, match="недоступна для записи"):
        check_data_dir_writable(settings)


def test_check_catalog_loaded_warns_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CATALOG_DATA", {})
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)
    warning = check_catalog_loaded(settings)
    assert warning is not None
    assert "catalog.json" in warning or str(settings.catalog_path) in warning


def test_check_catalog_loaded_silent_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CATALOG_DATA", {"services_list": ["Катафалк"]})
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)
    assert check_catalog_loaded(settings) is None


async def test_check_telegram_token_passes_and_logs_username():
    bot = AsyncMock()
    bot.get_me.return_value = AsyncMock(username="test_bot")
    await check_telegram_token(bot)
    bot.get_me.assert_awaited_once()


async def test_check_telegram_token_raises_preflight_error_on_failure():
    bot = AsyncMock()
    bot.get_me.side_effect = RuntimeError("401 Unauthorized")
    with pytest.raises(PreflightError, match="TELEGRAM_BOT_TOKEN"):
        await check_telegram_token(bot)


async def test_run_preflight_raises_immediately_without_gemini_key(tmp_path):
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    bot = AsyncMock()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await run_preflight(settings, bot)
    bot.get_me.assert_not_awaited()  # fails fast, never even reaches the network call


async def test_run_preflight_passes_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CATALOG_DATA", {"services_list": []})
    settings = Settings(gemini_api_key="dummy", data_dir=tmp_path)
    bot = AsyncMock()
    bot.get_me.return_value = AsyncMock(username="test_bot")

    await run_preflight(settings, bot)  # must not raise
