import pytest

from src.order_conflicts import build_order_summary
from src.order_store import CANCELLED, ENTERED, PENDING, OrderStore


@pytest.fixture
async def store(tmp_path):
    s = OrderStore(tmp_path / "orders.db")
    await s.init()
    return s


def _summary():
    return build_order_summary({
        "deceased": {"fio": "Тест Тестович"},
        "customer": {"fio": "Заказчик"},
        "services": [{"name": "Катафалк", "price": 2000, "1c_search_key": "Катафалк"}],
        "handwritten_total": 2000,
        "calculated_total": 2000,
    })


async def test_create_and_get_pending(store):
    record = await store.create_pending(
        "ORD-1", chat_id=42, photo_paths=["a.jpg"], num_addresses=1,
        order_data={"deceased": {"fio": "Тест Тестович"}}, summary=_summary(),
    )
    assert record.status == PENDING

    fetched = await store.get("ORD-1")
    assert fetched is not None
    assert fetched.deceased_fio == "Тест Тестович"
    assert fetched.photo_paths == ["a.jpg"]
    assert fetched.order_data == {"deceased": {"fio": "Тест Тестович"}}


async def test_get_missing_order_returns_none(store):
    assert await store.get("does-not-exist") is None


async def test_latest_pending_for_chat(store):
    await store.create_pending("ORD-1", 1, ["a.jpg"], 0, {}, _summary())
    await store.create_pending("ORD-2", 1, ["b.jpg"], 0, {}, _summary())
    await store.create_pending("ORD-3", 2, ["c.jpg"], 0, {}, _summary())  # different chat

    latest = await store.latest_pending_for_chat(1)
    assert latest.order_id == "ORD-2"


async def test_latest_pending_ignores_resolved_orders(store):
    await store.create_pending("ORD-1", 1, ["a.jpg"], 0, {}, _summary())
    await store.set_status("ORD-1", ENTERED)

    assert await store.latest_pending_for_chat(1) is None


async def test_set_status_with_error_message(store):
    await store.create_pending("ORD-1", 1, ["a.jpg"], 0, {}, _summary())
    await store.set_status("ORD-1", CANCELLED, error_message="Отменено пользователем")

    record = await store.get("ORD-1")
    assert record.status == CANCELLED
    assert record.error_message == "Отменено пользователем"


async def test_recent_orders_ordered_and_limited(store):
    for i in range(5):
        await store.create_pending(f"ORD-{i}", 1, [], 0, {}, _summary())

    recent = await store.recent(chat_id=1, limit=3)
    assert len(recent) == 3
    assert recent[0].order_id == "ORD-4"  # most recent first
