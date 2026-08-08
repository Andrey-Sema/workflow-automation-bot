"""End-to-end run of the base наряд against the real catalog — no Gemini,
no RDP, no 1C.

`tests/fixtures/order_base.json` is a transcription of the reference order
form (personal data replaced, every sum/quantity/name kept exactly), and
`data/catalog.sample.json` is the production catalog. Together they pin down
what the deterministic half of the pipeline actually does with real input,
so a change in the rules that quietly re-maps a line shows up here rather
than in 1C.

`tools/simulate_order.py` prints the same run in readable form.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import agent_logic, config
from src.errors import OneCInputError
from src.onec_order_entry import OneCOrderEntryBot, OrderCategory
from src.order_conflicts import build_order_summary
from src.settings import Settings
from src.validator import validate_and_fix_order

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.sample.json"
ORDER = ROOT / "tests" / "fixtures" / "order_base.json"

pytestmark = pytest.mark.skipif(
    not (CATALOG.exists() and ORDER.exists()), reason="нет каталога или фикстуры наряда"
)


@pytest.fixture
def base_order(monkeypatch):
    """Runs the real catalog through the real rules, then restores the
    process-wide catalog so no other test sees the swap."""
    saved = (config.CATALOG_DATA, config.SERVICES_LIST, config.SERVICES_JSON)
    config.load_catalog_from(CATALOG)
    agent_logic.refresh_from_catalog()

    raw = json.loads(ORDER.read_text(encoding="utf-8"))
    raw.pop("_meta", None)
    processed = agent_logic.apply_business_rules_in_python(raw, num_addresses=0, booked_in_1c=[])
    yield validate_and_fix_order(processed)

    config.CATALOG_DATA, config.SERVICES_LIST, config.SERVICES_JSON = saved
    agent_logic.refresh_from_catalog()


def line(order: dict, category: str, needle: str) -> dict | None:
    return next((i for i in order[category] if needle.lower() in i["name"].lower()), None)


# --- суммы ---

def test_calculated_total_matches_the_sum_of_lines(base_order):
    expected = sum(
        item["price"]
        for category in ("services", "goods", "transport")
        for item in base_order[category]
    )
    assert base_order["calculated_total"] == expected == 37180


def test_form_total_mismatch_is_reported_to_the_operator(base_order):
    summary = build_order_summary(base_order)
    assert summary.handwritten_total == 37830
    assert summary.sum_mismatch is True
    assert summary.sum_diff == 650


def test_no_line_is_lost_or_invented(base_order):
    raw = json.loads(ORDER.read_text(encoding="utf-8"))
    assert len(base_order["services"]) == len(raw["services"])
    assert len(base_order["goods"]) == len(raw["goods"])
    assert len(base_order["transport"]) == len(raw["transport"])


# --- позиции, которые обязаны сопоставиться ---

@pytest.mark.parametrize("category, needle, search_key, down", [
    ("goods", "Хрест Бублик", "Хрест Б", 1),           # Хрест 1710
    ("goods", "Вінок Ромашка", "Вінок Р", 0),          # Вінок 710
    ("goods", "Хрестик на труну 300", "Хрестик н", 4),
    ("goods", "Отче 220", "Отче", 0),                  # Рушник на кришку труни 220
    ("goods", "Табличка (времянка)", "Табличка в", 0), # Табличка тимчасова 300
    ("transport", "Доставка(Стандарт)", "Доставка (", 3),
])
def test_known_lines_map_to_the_expected_1c_entry(base_order, category, needle, search_key, down):
    item = line(base_order, category, needle)
    assert item is not None, f"строка '{needle}' пропала из наряда"
    assert item["1c_search_key"] == search_key
    assert item["1c_down_presses"] == down


# --- позиции, которые обязаны НЕ сопоставиться ---

def test_coffin_at_a_price_absent_from_the_catalog_is_not_guessed(base_order):
    """Труна за 3800 грн: в каталоге такой цены нет (ближайшие 3650 и 3900).
    Подставлять соседнюю цену нельзя — гроб не та позиция, где угадывают."""
    item = line(base_order, "goods", "Труна")
    assert item["price"] == 3800
    assert not item.get("1c_search_key")


def test_towel_at_a_price_absent_from_the_catalog_is_not_guessed(base_order):
    """Рушник на хрест за 380: в каталоге есть 250 и 350. Ни один не 380."""
    item = line(base_order, "goods", "Рушник на хрест")
    assert item["price"] == 380
    assert not item.get("1c_search_key")


def test_grave_mound_is_not_mapped_to_corpse_transfer(base_order):
    """Обе позиции стоят 1200 грн. Это совпадение цены, а не смысл:
    раньше «Насипний пагорб» уходил в 1С как «Перевезення покійного»."""
    assert line(base_order, "services", "Перевезення") is None
    item = line(base_order, "services", "Насипний пагорб")
    assert item is not None
    assert not item.get("1c_search_key")


# --- сводка и ввод в 1С ---

def test_summary_blocks_full_auto_entry_and_lists_the_manual_lines(base_order):
    summary = build_order_summary(base_order)
    assert summary.deceased_fio
    assert summary.cemetery == "Западное"
    assert summary.has_conflicts
    assert not summary.can_auto_enter
    assert "Труна" in summary.unmapped_items
    assert "Катафалк" in summary.unmapped_items


def test_every_mapped_line_can_be_typed_into_1c(base_order):
    """Всё, что получило search_key, должно проходить dry-run ввода без
    исключений — иначе оператор упрётся в ошибку на середине наряда."""
    bot = OneCOrderEntryBot(settings=Settings(onec_dry_run=True))
    mapped = {
        c: [i for i in base_order[c] if i.get("1c_search_key")]
        for c in ("services", "goods", "transport")
    }
    entered = bot.enter_order(mapped)
    assert len(entered) == sum(len(v) for v in mapped.values())
    assert len(entered) >= 5


def test_unmapped_line_is_refused_rather_than_typed_blindly(base_order):
    bot = OneCOrderEntryBot(settings=Settings(onec_dry_run=True))
    unmapped = next(i for i in base_order["goods"] if not i.get("1c_search_key"))
    with pytest.raises(OneCInputError):
        bot.enter_item(OrderCategory.GOODS, unmapped)


# --- доп. точки на реальном наряде ---

def test_extra_addresses_add_staff_and_transport_lines(monkeypatch):
    config.load_catalog_from(CATALOG)
    agent_logic.refresh_from_catalog()
    raw = json.loads(ORDER.read_text(encoding="utf-8"))
    raw.pop("_meta", None)

    result = agent_logic.apply_business_rules_in_python(raw, num_addresses=2, booked_in_1c=[])

    # Строка доп. точки создаётся под именем «снос (доп. точка)», после чего
    # маппинг переименовывает её в номенклатуру 1С («Персонал (ДОП
    # ТОЧКА/ГОДИНА)», тоже 500 грн) — поэтому ищем по тарифу, а не по имени.
    extra_staff = next(s for s in result["services"] if s.get("unit_price_for_1c") == 500)
    extra_transport = next(t for t in result["transport"] if "точка" in t["name"].lower())
    # 4 «снос» + 1 церемониймейстер = 5 человек, на 2 адреса = 10 точек
    assert extra_staff["quantity"] == 10
    assert extra_staff["price"] == 10 * 500
    # 2 машины (катафалк + доставка) на 2 адреса
    assert extra_transport["quantity"] == 4
    assert extra_transport["price"] == 4 * 1000
