"""Property-based tests (Hypothesis) for the business rules.

Example-based tests pin down the orders we've actually seen. These pin down
what must hold for *every* order — including the shapes a handwritten form
will eventually produce that nobody thought to write a case for: a zero
price, a quantity of 900, a name that is one Cyrillic character, a total
that doesn't divide evenly.

The invariants below are deliberately about **money and safety**, not about
which catalog line got picked: the pipeline is allowed to choose
differently as rules evolve, but it is never allowed to invent money or to
hand 1C a line it hasn't confirmed.
"""

from __future__ import annotations

import copy

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from src import agent_logic
from src.catalog_schema import validate_catalog
from src.order_conflicts import build_order_summary
from src.validator import validate_and_fix_order

# Hypothesis regenerates data per example while these tests monkeypatch
# module-level catalog state; function_scoped_fixture is exactly the
# combination being used deliberately here.
SUPPRESS = settings(
    max_examples=150,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)

CATALOG = {
    "services": [
        {"name": "снос (Галстук)", "price": 1550, "search_key": "снос", "dropdown_index": 9},
        {"name": "снос (Почесний Ескорт)", "price": 2400, "search_key": "снос", "dropdown_index": 12},
        {"name": "Перевезення покійного", "price": 1200, "search_key": "Перевезення", "dropdown_index": 2},
    ],
    "coffins": [
        {"name": "Труна Економ (Эконом)", "price": 2700, "search_key": "Труна Е", "dropdown_index": 0},
        {"name": "Труна Хадес дитячий", "price": 2700, "search_key": "Труна Х", "dropdown_index": 0},
        {"name": "Труна Велюр", "price": 3900, "search_key": "Труна В", "dropdown_index": 0},
    ],
    "towels": [
        {"name": "Отче 220", "price": 220, "search_key": "Отче", "dropdown_index": 0},
        {"name": "Рушник на фото 350", "price": 350, "search_key": "Рушник н", "dropdown_index": 3},
    ],
}

SERVICES_LIST = [
    "Катафалк", "Пронос труни", "Закопування/опускання труни", "снос", "снос-ескорт",
    "Насипний пагорб", "Встанов. хреста", "Церемоніймейстер", "Труна", "Хрест",
]

TARIFFS = {"extra_point": 500, "transport_base": 1000, "snos_base": 1550}
DIGGING = {"kopka_person_count": 4, "base_price_per_person": 1925, "towel_prices": [1400, 2000]}
PACKAGES = {"6200": {"name": "снос", "qty": 4}, "9600": {"name": "снос-ескорт", "qty": 4}}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING", copy.deepcopy(CATALOG))
    monkeypatch.setattr(agent_logic, "SERVICES_LIST", list(SERVICES_LIST))
    monkeypatch.setattr(agent_logic, "TARIFFS", dict(TARIFFS))
    monkeypatch.setattr(agent_logic, "DIGGING_RULES", dict(DIGGING))
    monkeypatch.setattr(agent_logic, "PERSONNEL_PACKAGES", copy.deepcopy(PACKAGES))
    monkeypatch.setattr(agent_logic, "KNOWN_UNIT_PRICES", {"Хусточки": [40]})
    agent_logic.find_best_service_name.cache_clear()
    yield
    agent_logic.find_best_service_name.cache_clear()


# Names drawn from the real vocabulary as well as arbitrary text: OCR output
# is neither purely one nor the other.
names = st.one_of(
    st.sampled_from([*SERVICES_LIST, "Труна Економ", "Отче 350", "Хусточки", "Рушник на хрест", ""]),
    st.text(max_size=40),
)
line_items = st.builds(
    lambda n, p, q: {"name": n, "price": p, "quantity": q},
    names,
    st.integers(min_value=0, max_value=1_000_000),
    st.integers(min_value=0, max_value=50),
)
orders = st.fixed_dictionaries({
    "services": st.lists(line_items, max_size=8),
    "goods": st.lists(line_items, max_size=8),
    "transport": st.lists(line_items, max_size=4),
})


# --- деньги ---

@SUPPRESS
@given(order=orders, num_addresses=st.integers(min_value=0, max_value=7))
def test_calculated_total_always_equals_the_sum_of_lines(order, num_addresses):
    """Единственный инвариант, который оператор проверяет глазами: итог
    обязан быть суммой строк. Любое правило — деление закопки, доп. точки,
    починка количества — не должно его расходить."""
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), num_addresses, [])
    expected = sum(
        item.get("price", 0)
        for category in ("services", "goods", "transport")
        for item in result[category]
    )
    assert result["calculated_total"] == expected


@SUPPRESS
@given(order=orders)
def test_no_line_ever_gets_a_negative_price_or_quantity(order):
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), 0, [])
    for category in ("services", "goods", "transport"):
        for item in result[category]:
            assert item.get("price", 0) >= 0
            assert item.get("quantity", 1) >= 0
            assert item.get("unit_price_for_1c", 0) >= 0


@SUPPRESS
@given(order=orders)
def test_unit_price_never_exceeds_the_line_total(order):
    """unit_price_for_1c уходит в 1С как цена за штуку. Если она больше
    суммы строки, в 1С уедет сумма больше, чем в бланке."""
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), 0, [])
    for category in ("services", "goods", "transport"):
        for item in result[category]:
            unit, total = item.get("unit_price_for_1c", 0), item.get("price", 0)
            qty = max(1, item.get("quantity", 1))
            assert unit * qty <= total + qty  # +qty: допуск на целочисленное деление


@SUPPRESS
@given(order=orders, num_addresses=st.integers(min_value=0, max_value=7))
def test_pipeline_output_always_survives_validation(order, num_addresses):
    """Всё, что выпускают бизнес-правила, обязано проходить Pydantic —
    иначе позиция молча выбрасывается из наряда на этапе валидации."""
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), num_addresses, [])
    before = sum(len(result[c]) for c in ("services", "goods", "transport"))
    validated = validate_and_fix_order(result)
    after = sum(len(validated[c]) for c in ("services", "goods", "transport"))
    assert after == before


# --- сопоставление с 1С ---

@SUPPRESS
@given(order=orders)
def test_a_mapped_line_never_carries_a_blank_search_key(order):
    """onec_order_entry печатает search_key вслепую. Пустой ключ означал бы
    ввод в 1С пустой строки поиска и выбор случайной номенклатуры."""
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), 0, [])
    for category in ("services", "goods", "transport"):
        for item in result[category]:
            key = item.get("1c_search_key")
            if key is not None:
                assert key.strip(), f"пустой search_key у {item.get('name')!r}"
                assert item.get("1c_down_presses", 0) >= 0


@SUPPRESS
@given(name=names, price=st.integers(min_value=0, max_value=100_000),
       category=st.sampled_from(["coffins", "towels", "services"]))
def test_mapping_never_picks_an_entry_at_a_different_price_by_partial_name(name, price, category):
    """Ключевое правило безопасности: частичное совпадение названия при
    другой цене — это другой вариант товара. Исключение только для точного
    совпадения имени (там оператор сам переставил цену)."""
    item = {"name": name, "price": price, "quantity": 1, "unit_price_for_1c": price}
    if not agent_logic._apply_1c_mapping(item, category, []):
        return

    entry = next(e for e in CATALOG[category] if e["name"] == item["name"])
    if item.get("1c_match_reason") not in ("name_exact", "name_loose", "alias"):
        assert entry["price"] == price


@SUPPRESS
@given(name=names, price=st.integers(min_value=0, max_value=100_000),
       category=st.sampled_from(["coffins", "towels", "services"]))
def test_mapping_is_idempotent(name, price, category):
    """Повторный прогон не должен переставлять позицию на другую строку 1С."""
    item = {"name": name, "price": price, "quantity": 1, "unit_price_for_1c": price}
    if not agent_logic._apply_1c_mapping(item, category, []):
        return
    first = (item["name"], item["1c_search_key"], item["1c_down_presses"])

    agent_logic._apply_1c_mapping(item, category, [])
    assert (item["name"], item["1c_search_key"], item["1c_down_presses"]) == first


@SUPPRESS
@given(name=names)
def test_normalized_name_is_either_official_or_untouched(name):
    """Нормализация либо приводит к названию из справочника, либо оставляет
    строку как в бланке. Придумывать третье она не должна."""
    result = agent_logic.find_best_service_name(name)
    assert result in SERVICES_LIST or result == name


@SUPPRESS
@given(name=names)
def test_zero_price_lines_are_never_partially_matched(name):
    """Позиция «б/ч» без цены: «Труна» — подстрока сотни наименований,
    выбор был бы случайным."""
    item = {"name": name, "price": 0, "quantity": 1, "unit_price_for_1c": 0}
    if agent_logic._apply_1c_mapping(item, "coffins", []):
        assert item["1c_match_reason"] in ("name_exact", "name_loose", "alias")


# --- пакеты персонала ---

@SUPPRESS
@given(qty=st.integers(min_value=0, max_value=20))
def test_personnel_package_always_reconciles_to_the_line_total(qty):
    """Расшифровка пакета меняет количество и цену за человека, но сумма
    строки меняться не должна — это деньги из бланка."""
    service = {"name": "Послуги персоналу", "price": 6200, "quantity": qty, "unit_price_for_1c": 6200}
    agent_logic._apply_personnel_package(service, [])
    assert service["price"] == 6200
    assert service["unit_price_for_1c"] * service["quantity"] == 6200


# --- сводка ---

@SUPPRESS
@given(order=orders, handwritten=st.integers(min_value=0, max_value=1_000_000))
def test_summary_never_reports_auto_entry_while_lines_are_unmapped(order, handwritten):
    """can_auto_enter=True при наличии несопоставленных строк означало бы,
    что onec_order_entry упрётся в исключение на середине наряда."""
    order = dict(order, handwritten_total=handwritten, deceased={}, customer={})
    result = agent_logic.apply_business_rules_in_python(copy.deepcopy(order), 0, [])
    summary = build_order_summary(validate_and_fix_order(result))

    if summary.unmapped_items:
        assert not summary.can_auto_enter
    if summary.can_auto_enter:
        assert not summary.unmapped_items


@SUPPRESS
@given(handwritten=st.integers(min_value=1, max_value=10_000_000),
       calculated=st.integers(min_value=0, max_value=10_000_000))
def test_sum_mismatch_flag_matches_the_one_percent_tolerance(handwritten, calculated):
    summary = build_order_summary({
        "deceased": {}, "customer": {},
        "handwritten_total": handwritten, "calculated_total": calculated,
    })
    tolerance = max(1, handwritten * 0.01)
    assert summary.sum_mismatch == (abs(handwritten - calculated) > tolerance)
    assert summary.sum_diff == handwritten - calculated


# --- валидатор каталога ---

@SUPPRESS
@given(data=st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=20),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4),
    max_leaves=25,
))
def test_catalog_validation_never_raises_on_arbitrary_input(data):
    """Проверка каталога вызывается на старте и из /catalog_check. Она
    обязана вернуть отчёт о любом мусоре, а не упасть — иначе кривой
    catalog.json роняет бота вместо того, чтобы объяснить проблему."""
    report = validate_catalog(data)
    assert isinstance(report.errors, list)
    assert isinstance(report.warnings, list)
    if not isinstance(data, dict) or not data:
        assert not report.ok


@SUPPRESS
@given(
    price=st.integers(min_value=-1000, max_value=100_000),
    index=st.integers(min_value=-5, max_value=50),
    name=st.text(max_size=20),
    key=st.text(max_size=10),
)
def test_catalog_entry_errors_are_reported_not_raised(price, index, name, key):
    report = validate_catalog({
        "services_list": ["Катафалк"],
        "catalog_1c_mapping": {"coffins": [
            {"name": name, "price": price, "search_key": key, "dropdown_index": index}
        ]},
    })
    invalid = (not name.strip()) or price < 0 or index < 0
    assert report.ok != invalid


# --- нормализация дат ---

@SUPPRESS
@given(order=orders)
def test_validator_always_fills_a_death_date(order):
    order = dict(order, deceased={}, customer={})
    validated = validate_and_fix_order(order)
    assert validated["deceased"]["death_date"]


@SUPPRESS
@given(booked=st.lists(st.sampled_from(SERVICES_LIST), min_size=1, max_size=5),
       extra=st.lists(line_items, max_size=5))
def test_every_line_already_in_1c_is_dropped(booked, extra):
    """Позиция, уже вбитая в 1С, не должна попасть в наряд второй раз —
    отменить ввод в 1С нельзя.

    Проверяем по количеству, а не по имени: правило сравнивает название ДО
    нормализации, а после неё уцелевшие строки могут получить как раз то
    имя, которое числится в booked."""
    services = [{"name": name, "price": 100, "quantity": 1} for name in booked] + list(extra)
    booked_norm = {" ".join(b.lower().split()) for b in booked}
    dropped = sum(1 for s in services if " ".join(s["name"].lower().split()) in booked_norm)
    assume(dropped)

    result = agent_logic.apply_business_rules_in_python(
        {"services": services, "goods": [], "transport": []}, 0, booked
    )

    # Часть услуг переезжает в товары (правило S6), поэтому <=, но снятых
    # дублей должно быть ровно столько, сколько совпало.
    assert len(result["services"]) <= len(services) - dropped
    assert sum(1 for w in result["warnings"] if "дубликат" in w) == dropped
