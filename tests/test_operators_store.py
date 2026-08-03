from src.operators_store import OperatorsStore


def test_empty_store_returns_empty_list(tmp_path):
    store = OperatorsStore(tmp_path / "operators.json")
    assert store.list() == []


def test_add_and_list(tmp_path):
    store = OperatorsStore(tmp_path / "operators.json")
    store.add(111)
    store.add(222)
    assert store.list() == [111, 222]


def test_add_is_idempotent(tmp_path):
    store = OperatorsStore(tmp_path / "operators.json")
    store.add(111)
    store.add(111)
    assert store.list() == [111]


def test_remove_existing(tmp_path):
    store = OperatorsStore(tmp_path / "operators.json")
    store.add(111)
    assert store.remove(111) is True
    assert store.list() == []


def test_remove_missing_returns_false(tmp_path):
    store = OperatorsStore(tmp_path / "operators.json")
    assert store.remove(999) is False


def test_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "operators.json"
    path.write_text("not json", encoding="utf-8")
    store = OperatorsStore(path)
    assert store.list() == []
