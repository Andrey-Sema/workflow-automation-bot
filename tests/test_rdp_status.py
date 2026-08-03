from src.rdp_status import describe_rdp_status, is_rdp_connected, read_rdp_status
from src.settings import Settings


def test_unknown_when_no_file(tmp_path):
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert read_rdp_status(settings) == "unknown"
    assert "неизвестен" in describe_rdp_status(settings)
    assert is_rdp_connected(settings) is False


def test_connected(tmp_path):
    (tmp_path / ".rdp_status").write_text("connected", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert is_rdp_connected(settings) is True
    assert "✅" in describe_rdp_status(settings)


def test_disabled_is_not_connected(tmp_path):
    (tmp_path / ".rdp_status").write_text("disabled", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert is_rdp_connected(settings) is False
    assert "RDP_HOST" in describe_rdp_status(settings)


def test_connecting_is_not_yet_connected(tmp_path):
    (tmp_path / ".rdp_status").write_text("connecting", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert is_rdp_connected(settings) is False


def test_disconnected_shows_raw_detail(tmp_path):
    (tmp_path / ".rdp_status").write_text("disconnected (code 1)", encoding="utf-8")
    settings = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert is_rdp_connected(settings) is False
    assert "disconnected (code 1)" in describe_rdp_status(settings)
