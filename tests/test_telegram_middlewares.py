from types import SimpleNamespace

from src.operators_store import OperatorsStore
from src.settings import Settings
from src.telegram_bot.middlewares import check_access, extract_chat_id


def test_extract_chat_id_from_message_like():
    event = SimpleNamespace(chat=SimpleNamespace(id=123))
    assert extract_chat_id(event) == 123


def test_extract_chat_id_from_callback_query_like():
    event = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=456)))
    assert extract_chat_id(event) == 456


def test_extract_chat_id_missing_returns_none():
    assert extract_chat_id(SimpleNamespace()) is None


def test_check_access_allows_env_operator(tmp_path):
    settings = Settings(gemini_api_key=None, telegram_operator_ids=[1])
    store = OperatorsStore(tmp_path / "operators.json")
    assert check_access(1, settings, store) is True


def test_check_access_allows_env_admin(tmp_path):
    settings = Settings(gemini_api_key=None, telegram_admin_ids=[1])
    store = OperatorsStore(tmp_path / "operators.json")
    assert check_access(1, settings, store) is True


def test_check_access_allows_runtime_added_operator(tmp_path):
    settings = Settings(gemini_api_key=None)
    store = OperatorsStore(tmp_path / "operators.json")
    store.add(999)
    assert check_access(999, settings, store) is True


def test_check_access_denies_unknown(tmp_path):
    settings = Settings(gemini_api_key=None)
    store = OperatorsStore(tmp_path / "operators.json")
    assert check_access(12345, settings, store) is False


def test_check_access_denies_none_chat_id(tmp_path):
    settings = Settings(gemini_api_key=None, telegram_admin_ids=[1])
    store = OperatorsStore(tmp_path / "operators.json")
    assert check_access(None, settings, store) is False
