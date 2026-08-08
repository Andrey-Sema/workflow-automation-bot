"""The other two reference orders, run against the real catalog.

`order_base` has its own file because it is the order the rules were tuned
on. These two exist to stop it from being the *only* shape the pipeline
handles: between them they cover priceless "б/ч" lines, a personnel rate
absent from the tariffs, a digging line at exactly the base price, and
multi-quantity attributes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import agent_logic, config
from src.order_conflicts import build_order_summary
from src.validator import validate_and_fix_order

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.sample.json"
FIXTURES = ROOT / "tests" / "fixtures"

pytestmark = pytest.mark.skipif(not CATALOG.exists(), reason="нет data/catalog.sample.json")


def run_order(name: str, num_addresses: int = 0) -> dict:
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    raw.pop("_meta", None)
    processed = agent_logic.apply_business_rules_in_python(raw, num_addresses, [])
    return validate_and_fix_order(processed)


@pytest.fixture(autouse=True)
def real_catalog():
    saved = (config.CATALOG_DATA, config.SERVICES_LIST, config.SERVICES_JSON)
    config.load_catalog_from(CATALOG)
    agent_logic.refresh_from_catalog()
    yield
    config.CATALOG_DATA, config.SERVICES_LIST, config.SERVICES_JSON = saved
    agent_logic.refresh_from_catalog()


def find(order: dict, category: str, needle: str) -> dict | None:
    return next((i for i in order[category] if needle.lower() in i["name"].lower()), None)


# --- наряд 2: задаток, позиции «б/ч», ставка вне тарифов ---

def test_prepaid_order_totals_reconcile_exactly():
    order = run_order("order_prepaid")
    summary = build_order_summary(order)
    assert summary.calculated_total == 21820
    assert summary.handwritten_total == 21820
    assert not summary.sum_mismatch


@pytest.mark.parametrize("name", ["Труна", "Хрест", "Вінок", "Подушка", "Покривало"])
def test_priceless_lines_are_never_matched_to_a_catalog_item(name):
    """«б/ч» — позиция без цены (своя, от родственников). Сопоставлять её
    по названию нельзя: «Труна» — подстрока сотни наименований гробов,
    и выбор из них был бы случайным."""
    order = run_order("order_prepaid")
    item = find(order, "goods", name)
    assert item is not None
    assert item["price"] == 0
    assert not item.get("1c_search_key")


def test_personnel_rate_absent_from_the_catalog_is_not_guessed():
    """4 чел. по 2050 грн = 8200. Ни 2050, ни 8200 нет ни в tariffs,
    ни в personnel_packages — значит только ручной ввод."""
    order = run_order("order_prepaid")
    item = find(order, "services", "персоналу")
    assert item["quantity"] == 4
    assert item["unit_price_for_1c"] == 2050
    assert not item.get("1c_search_key")


def test_corpse_transfer_at_1200_maps_when_it_really_is_that_service():
    """Зеркало к базовому наряду: там 1200 грн принадлежали «Насипному
    пагорбу» и маппинг был отклонён. Здесь это действительно перевозка."""
    order = run_order("order_prepaid")
    item = find(order, "transport", "Перевезення")
    assert item["1c_search_key"] == "Перевезення"
    assert item["1c_down_presses"] == 2


def test_plaque_on_a_cross_reaches_the_plaques_catalog():
    order = run_order("order_prepaid")
    item = find(order, "goods", "Табличка")
    assert item["price"] == 750
    assert item["1c_search_key"] == "Табличка м"


# --- наряд 3: VIP, пакеты персонала, закопка ровно по тарифу ---

def test_vip_order_totals_reconcile_exactly():
    order = run_order("order_vip")
    summary = build_order_summary(order)
    assert summary.calculated_total == 176230
    assert summary.handwritten_total == 176230
    assert not summary.sum_mismatch


def test_digging_at_exactly_the_base_price_is_not_split():
    """7700 = 1925 x 4 — это ровно базовая копка без рушника. Правило
    отделения рушника срабатывает только от 7700 + 1400."""
    order = run_order("order_vip")
    digging = find(order, "services", "Закопування")
    assert digging["price"] == 7700
    assert digging["quantity"] == 1
    assert not any("Рушник для опускання" in i["name"] for i in order["goods"])


def test_both_personnel_lines_survive_with_their_own_rates():
    order = run_order("order_vip")
    personnel = [i for i in order["services"] if "персоналу" in i["name"].lower()]
    assert len(personnel) == 2
    assert {p["unit_price_for_1c"] for p in personnel} == {2400, 1550}


def test_three_way_price_ambiguity_is_resolved_by_name():
    """В towels три позиции по 350 грн. «Рушник на портрет» должен уйти
    в «Рушник на фото», а не в «Отче» или «Рушник на хрест»."""
    order = run_order("order_vip")
    item = find(order, "goods", "Рушник на фото")
    assert item is not None
    assert item["1c_search_key"] == "Рушник н"
    assert item["1c_down_presses"] == 3


@pytest.mark.parametrize("needle, search_key", [
    ("Хрест Квадрат", "Хрест К"),          # Хрест 5800
    ("Комплект рушників", "Комплект р"),   # 850
    ("Хрестик на труну 260", "Хрестик н"),
])
def test_vip_lines_that_must_map(needle, search_key):
    order = run_order("order_vip")
    item = find(order, "goods", needle)
    assert item is not None, f"строка '{needle}' не найдена"
    assert item["1c_search_key"] == search_key


def test_multi_quantity_attributes_keep_their_unit_price():
    order = run_order("order_vip")
    vase = find(order, "goods", "Ваза")
    assert vase["quantity"] == 2
    assert vase["unit_price_for_1c"] == 1250


def test_handkerchiefs_with_correct_quantity_are_left_alone():
    """50 x 40 = 2000 — количество уже верное, «лечить» нечего."""
    order = run_order("order_vip")
    item = find(order, "goods", "Хусточки")
    assert item["quantity"] == 50
    assert item["unit_price_for_1c"] == 40


def test_hearse_is_not_renamed_into_a_wreath_truck():
    """«Автотранспорт для поховання» набирал 0.72 схожести с
    «Автотранспорт під вінки» на общем префиксе — разные услуги."""
    assert agent_logic.find_best_service_name("Автотранспорт для поховання") != "Автотранспорт під вінки"


def test_missing_cemetery_does_not_break_the_summary():
    order = run_order("order_vip")
    summary = build_order_summary(order)
    assert summary.cemetery == ""
    assert summary.deceased_fio


# --- общее для всех трёх нарядов ---

@pytest.mark.parametrize("name", ["order_base", "order_prepaid", "order_vip"])
def test_no_order_loses_or_invents_money(name):
    """Бизнес-правила не должны менять итог: они переименовывают, делят и
    сопоставляют позиции, но сумма наряда обязана сойтись со строками."""
    order = run_order(name)
    expected = sum(
        item["price"] for c in ("services", "goods", "transport") for item in order[c]
    )
    assert order["calculated_total"] == expected


@pytest.mark.parametrize("name", ["order_base", "order_prepaid", "order_vip"])
def test_every_mapped_line_carries_a_usable_search_key(name):
    order = run_order(name)
    for category in ("services", "goods", "transport"):
        for item in order[category]:
            key = item.get("1c_search_key")
            if key:
                assert key.strip(), f"{item['name']}: search_key из пробелов"
                assert item.get("1c_down_presses", 0) >= 0
