"""Direct handler tests: call the aiogram handler coroutines with a real
FSMContext (MemoryStorage) and lightweight fakes for Message/CallbackQuery,
bypassing the Dispatcher's own routing (which is aiogram's tested code, not
ours). This exercises the actual state transitions and side effects.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.errors import OneCInputError
from src.order_conflicts import build_order_summary
from src.order_store import CANCELLED, ENTERED, FAILED, OrderStore
from src.settings import Settings
from src.telegram_bot import keyboards as kb
from src.telegram_bot.handlers import orders
from src.telegram_bot.states import OrderFlow


@pytest.fixture
def fsm():
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)
    return FSMContext(storage=storage, key=key)


@pytest.fixture
def order_settings(tmp_path):
    return Settings(gemini_api_key="dummy", data_dir=tmp_path, onec_dry_run=True)


@pytest.fixture
async def order_store(tmp_path):
    store = OrderStore(tmp_path / "orders.db")
    await store.init()
    return store


def fake_message(**overrides):
    base = {
        "answer": AsyncMock(), "edit_text": AsyncMock(), "answer_document": AsyncMock(),
        "chat": SimpleNamespace(id=42), "bot": SimpleNamespace(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def fake_callback(data: str, **overrides):
    base = {"data": data, "message": fake_message(), "answer": AsyncMock()}
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_cmd_cancel_clears_state(fsm):
    await fsm.set_state(OrderFlow.collecting)
    await fsm.update_data(photos=["a.jpg"])
    message = fake_message()

    await orders.cmd_cancel(message, fsm)

    assert await fsm.get_state() is None
    message.answer.assert_awaited_once()


async def test_receive_photo_starts_collecting(fsm, order_settings):
    message = fake_message(photo=None, document=None)
    with patch("src.telegram_bot.handlers.orders.download_order_photo", new=AsyncMock(return_value=Path("/tmp/a.jpg"))):
        await orders.receive_photo(message, fsm, order_settings)

    data = await fsm.get_data()
    assert data["photos"] == ["/tmp/a.jpg"]
    assert await fsm.get_state() == OrderFlow.collecting.state
    message.answer.assert_awaited_once()


async def test_receive_photo_appends_to_existing_draft(fsm, order_settings):
    await fsm.update_data(draft_id="abc123", photos=["/tmp/first.jpg"])
    message = fake_message(photo=None, document=None)

    download_mock = AsyncMock(return_value=Path("/tmp/second.jpg"))
    with patch("src.telegram_bot.handlers.orders.download_order_photo", new=download_mock):
        await orders.receive_photo(message, fsm, order_settings)

    data = await fsm.get_data()
    assert data["photos"] == ["/tmp/first.jpg", "/tmp/second.jpg"]
    assert data["draft_id"] == "abc123"


async def test_cancel_draft_clears_state(fsm):
    await fsm.set_state(OrderFlow.collecting)
    callback = fake_callback(kb.CANCEL_DRAFT_CB)

    await orders.cancel_draft(callback, fsm)

    assert await fsm.get_state() is None
    callback.message.edit_text.assert_awaited_once()


async def test_photos_done_requires_at_least_one_photo(fsm):
    callback = fake_callback(kb.DONE_CB)
    await orders.photos_done(callback, fsm)
    callback.answer.assert_awaited_once_with("Сначала пришли хотя бы одно фото.", show_alert=True)


async def test_photos_done_advances_state(fsm):
    await fsm.update_data(photos=["/tmp/a.jpg"])
    callback = fake_callback(kb.DONE_CB)

    await orders.photos_done(callback, fsm)

    assert await fsm.get_state() == OrderFlow.choosing_addresses.state


async def test_addresses_chosen_stores_count(fsm):
    callback = fake_callback(f"{kb.ADDRESSES_CB_PREFIX}3")
    await orders.addresses_chosen(callback, fsm)

    data = await fsm.get_data()
    assert data["num_addresses"] == 3


async def test_confirm_enter_missing_order_shows_alert(fsm, order_settings, order_store):
    callback = fake_callback(f"{kb.CONFIRM_ENTER_CB}NOPE")
    await orders.confirm_enter(callback, fsm, order_settings, order_store)
    callback.answer.assert_awaited_once_with("Этот наряд уже обработан или не найден.", show_alert=True)


async def test_confirm_enter_success_moves_files_and_marks_entered(fsm, order_settings, order_store, tmp_path):
    photo = tmp_path / "incoming" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"fake")

    order_data = {"deceased": {"fio": "Тест"}, "services": [], "goods": [], "transport": []}
    summary = build_order_summary(order_data)
    await order_store.create_pending("ORD1", 42, [str(photo)], 0, order_data, summary)

    callback = fake_callback(f"{kb.CONFIRM_ENTER_CB}ORD1")
    await orders.confirm_enter(callback, fsm, order_settings, order_store)

    record = await order_store.get("ORD1")
    assert record.status == ENTERED
    assert not photo.exists()  # moved out of incoming/
    assert (order_settings.processed_dir / "a.jpg").exists()


async def test_confirm_enter_failure_keeps_files_and_marks_failed(fsm, order_settings, order_store, tmp_path):
    photo = tmp_path / "incoming" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"fake")

    order_data = {"deceased": {"fio": "Тест"}, "services": [{"name": "no key"}], "goods": [], "transport": []}
    summary = build_order_summary(order_data)
    await order_store.create_pending("ORD1", 42, [str(photo)], 0, order_data, summary)

    callback = fake_callback(f"{kb.CONFIRM_ENTER_CB}ORD1")
    with patch("src.telegram_bot.handlers.orders.OneCOrderEntryBot") as mock_bot_cls:
        mock_bot_cls.return_value.enter_order.side_effect = OneCInputError("boom")
        await orders.confirm_enter(callback, fsm, order_settings, order_store)

    record = await order_store.get("ORD1")
    assert record.status == FAILED
    assert photo.exists()  # left in place for manual review


async def test_confirm_cancel_marks_cancelled(fsm, order_store):
    order_data = {"deceased": {"fio": "Тест"}}
    summary = build_order_summary(order_data)
    await order_store.create_pending("ORD1", 42, [], 0, order_data, summary)

    callback = fake_callback(f"{kb.CONFIRM_CANCEL_CB}ORD1")
    await orders.confirm_cancel(callback, fsm, order_store)

    record = await order_store.get("ORD1")
    assert record.status == CANCELLED


async def test_show_log_missing_file_shows_alert(order_settings):
    callback = fake_callback(f"{kb.SHOW_LOG_CB}NOPE")
    await orders.show_log(callback, order_settings)
    callback.answer.assert_awaited_once_with("Лог не найден.", show_alert=True)


def test_move_photos_to_processed_skips_missing_files(order_settings, caplog):
    orders._move_photos_to_processed(["/no/such/file.jpg"], order_settings)
    assert order_settings.processed_dir.exists()
