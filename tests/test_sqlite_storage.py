import pytest
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from src.telegram_bot.sqlite_storage import SQLiteStorage


class _Flow(StatesGroup):
    step_one = State()


def _key(chat_id: int = 42) -> StorageKey:
    return StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id)


@pytest.fixture
async def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "fsm.db")
    await s.init()
    return s


async def test_get_state_defaults_to_none(storage):
    assert await storage.get_state(_key()) is None


async def test_get_data_defaults_to_empty_dict(storage):
    assert await storage.get_data(_key()) == {}


async def test_set_and_get_state_with_state_object(storage):
    await storage.set_state(_key(), _Flow.step_one)
    assert await storage.get_state(_key()) == _Flow.step_one.state


async def test_set_and_get_state_with_plain_string(storage):
    await storage.set_state(_key(), "some:state")
    assert await storage.get_state(_key()) == "some:state"


async def test_set_state_none_clears_state_but_keeps_data(storage):
    await storage.set_data(_key(), {"photos": ["a.jpg"]})
    await storage.set_state(_key(), _Flow.step_one)

    await storage.set_state(_key(), None)

    assert await storage.get_state(_key()) is None
    assert await storage.get_data(_key()) == {"photos": ["a.jpg"]}


async def test_set_data_replaces_full_dict(storage):
    await storage.set_data(_key(), {"a": 1, "b": 2})
    await storage.set_data(_key(), {"c": 3})
    assert await storage.get_data(_key()) == {"c": 3}


async def test_set_data_does_not_clear_existing_state(storage):
    await storage.set_state(_key(), _Flow.step_one)
    await storage.set_data(_key(), {"a": 1})
    assert await storage.get_state(_key()) == _Flow.step_one.state


async def test_update_data_merges(storage):
    """update_data() is inherited from BaseStorage (get -> dict.update -> set) —
    exercising it here doubles as a regression check that our set_data/get_data
    round-trip plays nicely with that default implementation."""
    await storage.set_data(_key(), {"a": 1})
    result = await storage.update_data(_key(), {"b": 2})
    assert result == {"a": 1, "b": 2}
    assert await storage.get_data(_key()) == {"a": 1, "b": 2}


async def test_different_chats_are_isolated(storage):
    await storage.set_data(_key(1), {"photos": ["one.jpg"]})
    await storage.set_data(_key(2), {"photos": ["two.jpg"]})

    assert await storage.get_data(_key(1)) == {"photos": ["one.jpg"]}
    assert await storage.get_data(_key(2)) == {"photos": ["two.jpg"]}


async def test_survives_reopening_the_same_db_file(tmp_path):
    """The whole point: data must still be there after the process
    restarts (a fresh SQLiteStorage instance pointed at the same file)."""
    db_path = tmp_path / "fsm.db"

    first = SQLiteStorage(db_path)
    await first.init()
    await first.set_state(_key(), _Flow.step_one)
    await first.set_data(_key(), {"draft_id": "abc123", "photos": ["a.jpg", "b.jpg"]})

    second = SQLiteStorage(db_path)
    await second.init()

    assert await second.get_state(_key()) == _Flow.step_one.state
    assert await second.get_data(_key()) == {"draft_id": "abc123", "photos": ["a.jpg", "b.jpg"]}


async def test_unicode_data_round_trips(storage):
    await storage.set_data(_key(), {"deceased_fio": "Тестенко Тест Тестович"})
    assert await storage.get_data(_key()) == {"deceased_fio": "Тестенко Тест Тестович"}


async def test_close_is_a_safe_noop(storage):
    await storage.close()
