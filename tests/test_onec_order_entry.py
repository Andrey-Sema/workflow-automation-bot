import pytest

from src.errors import OneCInputError
from src.onec_order_entry import CATEGORY_TAB_MAP, FieldNav, OneCOrderEntryBot, OrderCategory
from src.settings import Settings


def make_settings(**overrides) -> Settings:
    base = {
        "gemini_api_key": "dummy",
        "onec_dry_run": True,
        "onec_action_pause": 0.0,
        "onec_keystroke_delay": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_dry_run_never_touches_pyautogui(monkeypatch):
    """In dry-run mode (the default) no real pyautogui call should ever happen."""
    import src.onec_order_entry as mod

    monkeypatch.setattr(mod, "pyautogui", None)  # would AttributeError immediately if actually called

    bot = OneCOrderEntryBot(settings=make_settings())
    order = {
        "services": [{"name": "Катафалк", "price": 2000, "quantity": 1,
                       "1c_search_key": "Катафалк", "1c_down_presses": 0, "unit_price_for_1c": 2000}],
        "goods": [],
        "transport": [],
    }
    entered = bot.enter_order(order)
    assert entered == ["Катафалк"]


def test_missing_search_key_raises_friendly_error():
    bot = OneCOrderEntryBot(settings=make_settings())
    with pytest.raises(OneCInputError):
        bot.enter_item(OrderCategory.SERVICES, {"name": "Без ключа", "price": 100, "quantity": 1})


def test_enter_order_stops_on_first_failure():
    bot = OneCOrderEntryBot(settings=make_settings())
    order = {
        "services": [
            {"name": "OK", "price": 100, "quantity": 1, "1c_search_key": "OK"},
            {"name": "Bad", "price": 100, "quantity": 1},  # no search key -> should raise
            {"name": "Never reached", "price": 100, "quantity": 1, "1c_search_key": "X"},
        ],
        "goods": [],
        "transport": [],
    }
    with pytest.raises(OneCInputError):
        bot.enter_order(order)


def test_live_mode_calls_pyautogui_in_expected_sequence(monkeypatch):
    """Not dry-run: verify the click/type/press sequence without a real display."""
    import src.onec_order_entry as mod

    calls = []

    class FakePyAutoGUI:
        def write(self, text, interval=0):
            calls.append(("write", text))

        def press(self, key, presses=1, interval=0):
            calls.append(("press", key, presses))

    monkeypatch.setattr(mod, "pyautogui", FakePyAutoGUI())
    monkeypatch.setattr(mod, "click_tab_by_image", lambda template, confidence: True)

    bot = OneCOrderEntryBot(settings=make_settings(onec_dry_run=False), field_nav=FieldNav(price_autofills=True))
    item = {
        "name": "Катафалк", "price": 2000, "quantity": 1,
        "1c_search_key": "Катафалк К", "1c_down_presses": 2, "unit_price_for_1c": 2000,
    }
    bot.enter_item(OrderCategory.SERVICES, item)

    assert ("write", "Катафалк К") in calls
    assert ("press", "down", 2) in calls
    assert ("write", "1") in calls  # quantity


def test_category_tab_map_covers_all_categories():
    for category in OrderCategory:
        assert category in CATEGORY_TAB_MAP
