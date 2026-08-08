import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import config
from src.errors import AccessDeniedError
from src.operators_store import OperatorsStore
from src.order_store import OrderStore
from src.settings import Settings
from src.telegram_bot.handlers import admin


def fake_message():
    return SimpleNamespace(answer=AsyncMock())


def fake_command(args: str | None):
    return SimpleNamespace(args=args)


@pytest.fixture
async def order_store(tmp_path):
    store = OrderStore(tmp_path / "orders.db")
    await store.init()
    return store


@pytest.fixture
def admin_settings(tmp_path):
    return Settings(gemini_api_key="dummy", data_dir=tmp_path, onec_dry_run=True)


@pytest.fixture
def operators_store(tmp_path):
    return OperatorsStore(tmp_path / "operators.json")


async def test_cmd_status_reports_catalog_and_mode(admin_settings, order_store):
    message = fake_message()
    await admin.cmd_status(message, admin_settings, order_store)
    text = message.answer.await_args.args[0]
    assert "dry-run" in text


@pytest.mark.parametrize("handler_name, extra_args", [
    ("cmd_tariffs", ()),
    ("cmd_reload_catalog", ()),
])
async def test_admin_only_commands_reject_non_admin(handler_name, extra_args):
    handler = getattr(admin, handler_name)
    message = fake_message()
    with pytest.raises(AccessDeniedError):
        await handler(message, False, *extra_args)


async def test_cmd_set_tariff_requires_two_args():
    message = fake_message()
    await admin.cmd_set_tariff(message, fake_command("extra_point"), True)
    assert "Использование" in message.answer.await_args.args[0]


async def test_cmd_set_tariff_rejects_non_integer():
    message = fake_message()
    await admin.cmd_set_tariff(message, fake_command("extra_point abc"), True)
    assert "целым числом" in message.answer.await_args.args[0]


async def test_cmd_set_tariff_updates_value(monkeypatch, tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"tariffs": {"extra_point": 500}}), encoding="utf-8")
    monkeypatch.setattr(config, "CATALOG_DATA", {"tariffs": {"extra_point": 500}})
    monkeypatch.setattr(config._settings, "data_dir", tmp_path)

    message = fake_message()
    await admin.cmd_set_tariff(message, fake_command("extra_point 700"), True)

    assert "700" in message.answer.await_args.args[0]
    assert config.CATALOG_DATA["tariffs"]["extra_point"] == 700


async def test_cmd_operators_lists_env_and_runtime(admin_settings, operators_store, monkeypatch):
    monkeypatch.setattr(admin_settings, "telegram_operator_ids", [111])
    operators_store.add(222)
    message = fake_message()

    await admin.cmd_operators(message, True, admin_settings, operators_store)

    text = message.answer.await_args.args[0]
    assert "111" in text
    assert "222" in text


async def test_cmd_add_operator_invalid_id(operators_store):
    message = fake_message()
    await admin.cmd_add_operator(message, fake_command("not-a-number"), True, operators_store)
    assert "Использование" in message.answer.await_args.args[0]


async def test_cmd_add_operator_success(operators_store):
    message = fake_message()
    await admin.cmd_add_operator(message, fake_command("555"), True, operators_store)
    assert 555 in operators_store.list()


async def test_cmd_remove_operator_not_present(operators_store):
    message = fake_message()
    await admin.cmd_remove_operator(message, fake_command("999"), True, operators_store)
    assert "не было" in message.answer.await_args.args[0] or "нет" in message.answer.await_args.args[0]


async def test_cmd_remove_operator_success(operators_store):
    operators_store.add(555)
    message = fake_message()
    await admin.cmd_remove_operator(message, fake_command("555"), True, operators_store)
    assert 555 not in operators_store.list()


# --- /catalog_check ---

async def test_cmd_catalog_check_reports_conflicts(monkeypatch):
    """Каталог с двумя позициями по одной цене должен сообщить об этом:
    такие неоднозначности не падают в рантайме, они молча вбивают в 1С
    не ту номенклатуру."""
    monkeypatch.setattr(config, "CATALOG_DATA", {
        "services_list": ["Катафалк"],
        "catalog_1c_mapping": {
            "coffins": [
                {"name": "Труна Економ", "price": 2700, "search_key": "Труна Е", "dropdown_index": 0},
                {"name": "Труна Хадес", "price": 2700, "search_key": "Труна Х", "dropdown_index": 0},
            ]
        },
    })
    message = fake_message()
    await admin.cmd_catalog_check(message, is_admin=True)

    text = message.answer.await_args.args[0]
    assert "DUPLICATE_PRICE" in text
    assert "2700" in text


async def test_cmd_catalog_check_on_a_clean_catalog(monkeypatch):
    monkeypatch.setattr(config, "CATALOG_DATA", {"services_list": ["Катафалк"]})
    message = fake_message()
    await admin.cmd_catalog_check(message, is_admin=True)
    assert "ошибок: 0" in message.answer.await_args.args[0]


async def test_cmd_catalog_check_requires_admin():
    with pytest.raises(AccessDeniedError):
        await admin.cmd_catalog_check(fake_message(), is_admin=False)


async def test_cmd_catalog_check_truncates_a_huge_report(monkeypatch):
    """Telegram обрывает сообщение на 4096 символах — отчёт по каталогу
    с сотней коллизий не должен превращать команду в ошибку отправки."""
    monkeypatch.setattr(config, "CATALOG_DATA", {
        "services_list": ["Катафалк"],
        "catalog_1c_mapping": {
            "coffins": [
                {"name": f"Труна вариант {i} с очень длинным описанием позиции",
                 "price": 1000, "search_key": "Труна в", "dropdown_index": i}
                for i in range(80)
            ]
        },
    })
    message = fake_message()
    await admin.cmd_catalog_check(message, is_admin=True)
    assert len(message.answer.await_args.args[0]) <= 4096


async def test_cmd_reload_catalog_mentions_conflicts(monkeypatch, admin_settings):
    monkeypatch.setattr(config, "CATALOG_DATA", {
        "services_list": ["Катафалк"],
        "catalog_1c_mapping": {
            "coffins": [
                {"name": "Труна А", "price": 100, "search_key": "Труна А", "dropdown_index": 0},
                {"name": "Труна Б", "price": 100, "search_key": "Труна Б", "dropdown_index": 0},
            ]
        },
    })
    monkeypatch.setattr(admin.catalog_admin, "reload_catalog", lambda: config.CATALOG_DATA)

    message = fake_message()
    await admin.cmd_reload_catalog(message, is_admin=True)
    assert "/catalog_check" in message.answer.await_args.args[0]
