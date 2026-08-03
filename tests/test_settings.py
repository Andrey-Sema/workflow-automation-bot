import pytest

from src.settings import Settings, require_gemini_api_key, require_telegram_token


def test_settings_construct_by_field_name():
    """Regression: populate_by_name must be on, or field-name kwargs are
    silently swallowed by extra="ignore" instead of taking effect."""
    s = Settings(gemini_api_key="abc", onec_dry_run=False)
    assert s.gemini_api_key.get_secret_value() == "abc"
    assert s.onec_dry_run is False


def test_settings_defaults_are_safe():
    s = Settings(gemini_api_key=None)
    assert s.onec_dry_run is True
    assert s.telegram_admin_ids == []
    assert s.vision_model_name == "gemini-3.6-flash"
    assert s.text_model_name == "gemini-3.5-flash-lite"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("123,456", [123, 456]),
        (" 123 , 456 ", [123, 456]),
        ("", []),
        (None, []),
        ([1, 2], [1, 2]),
    ],
)
def test_telegram_id_list_parsing(raw, expected):
    s = Settings(gemini_api_key=None, telegram_admin_ids=raw)
    assert s.telegram_admin_ids == expected


def test_is_admin_and_is_operator():
    s = Settings(gemini_api_key=None, telegram_admin_ids=[1], telegram_operator_ids=[2])
    assert s.is_admin(1) is True
    assert s.is_admin(2) is False
    assert s.is_operator(2) is True
    assert s.is_operator(1) is True  # admins are always operators
    assert s.is_operator(3) is False


def test_blank_model_name_rejected():
    with pytest.raises(ValueError):
        Settings(gemini_api_key=None, vision_model_name="   ")


def test_require_gemini_api_key_raises_clear_error():
    s = Settings(gemini_api_key=None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        require_gemini_api_key(s)


def test_require_telegram_token_raises_clear_error():
    s = Settings(gemini_api_key=None, telegram_bot_token=None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        require_telegram_token(s)


def test_directory_properties_are_under_data_dir(tmp_path):
    s = Settings(gemini_api_key=None, data_dir=tmp_path)
    assert s.input_dir == tmp_path / "input"
    assert s.catalog_path == tmp_path / "catalog.json"
    s.ensure_directories()
    assert s.input_dir.is_dir()
    assert s.log_dir.is_dir()
