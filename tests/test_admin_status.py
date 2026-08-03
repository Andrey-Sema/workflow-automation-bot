from src.settings import Settings
from src.telegram_bot.handlers.admin import _read_rdp_status


def test_rdp_status_unknown_when_no_file(tmp_path):
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert "неизвестен" in _read_rdp_status(settings)


def test_rdp_status_connected(tmp_path):
    (tmp_path / ".rdp_status").write_text("connected", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert "✅" in _read_rdp_status(settings)


def test_rdp_status_disabled(tmp_path):
    (tmp_path / ".rdp_status").write_text("disabled", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert "RDP_HOST" in _read_rdp_status(settings)


def test_rdp_status_disconnected_shows_raw_detail(tmp_path):
    (tmp_path / ".rdp_status").write_text("disconnected (code 1)", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert "disconnected (code 1)" in _read_rdp_status(settings)
