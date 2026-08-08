"""Tests for how a line item is matched to a 1C nomenclature entry.

Every case here is a regression from a real order or the real catalog. The
shared theme: matching used to be price-only and first-hit-wins, so any two
items sharing a price were a coin flip, and a wrong flip is typed into 1C
with no undo.
"""

from __future__ import annotations

import pytest

from src import agent_logic


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING", {
        "towels": [
            {"name": "Рушник Льон", "price": 70, "search_key": "Рушник Л", "dropdown_index": 0},
            {"name": "Отче 220 (Рушник на крышку гроба-Отче)", "price": 220,
             "search_key": "Отче", "dropdown_index": 0},
            {"name": "Рушник на фото 350 (Рушник на фото 120)", "price": 350,
             "search_key": "Рушник н", "dropdown_index": 3},
            {"name": "Отче 350 (Отче 75 Рушник на крышку гроба)", "price": 350,
             "search_key": "Отче", "dropdown_index": 1},
        ],
        "coffins": [
            {"name": "Труна Хадес дитячий 0,8 м (горіх, біла)", "price": 2700,
             "search_key": "Труна Х", "dropdown_index": 0},
            {"name": "Труна Економ (Эконом)", "price": 2700, "search_key": "Труна Е", "dropdown_index": 0},
        ],
        "plaques": [
            {"name": "Табличка на хрест метал", "price": 780, "search_key": "Табличка н", "dropdown_index": 0},
        ],
        "crosses": [
            {"name": "Рушник на хрест 250 (Рушник на крест 75)", "price": 250,
             "search_key": "Рушник н", "dropdown_index": 0},
        ],
        "services": [
            {"name": "снос (Галстук)", "price": 1550, "search_key": "снос", "dropdown_index": 9},
            {"name": "снос (Почесний Ескорт)", "price": 2400, "search_key": "снос", "dropdown_index": 12},
            {"name": "Перевезення покійного", "price": 1200, "search_key": "Перевезення", "dropdown_index": 2},
        ],
    })
    monkeypatch.setattr(agent_logic, "SERVICES_LIST", [
        "Катафалк", "Насипний пагорб", "Встанов. хреста", "Хрест", "Пронос труни",
        "Закопування/опускання труни", "снос", "снос-ескорт", "снос (доп. точка)",
    ])
    agent_logic.find_best_service_name.cache_clear()
    yield
    agent_logic.find_best_service_name.cache_clear()


def mapped(name: str, price: int, category: str, qty: int = 1):
    item = {"name": name, "price": price, "quantity": qty, "unit_price_for_1c": price // max(1, qty)}
    warnings: list[str] = []
    ok = agent_logic._apply_1c_mapping(item, category, warnings)
    return ok, item, warnings


# --- имя решает, а не только цена ---

def test_отче_350_no_longer_becomes_рушник_на_фото():
    """Регресс: подбор шёл только по цене и брал ПЕРВОЕ совпадение, поэтому
    «Отче 350» уходило в 1С как «Рушник на фото 350» — обе позиции по 350."""
    ok, item, _ = mapped("Отче 350", 350, "towels")
    assert ok
    assert item["name"].startswith("Отче 350")
    assert item["1c_search_key"] == "Отче"
    assert item["1c_down_presses"] == 1


def test_рушник_на_фото_still_maps_to_itself():
    ok, item, _ = mapped("Рушник на фото", 350, "towels")
    assert ok
    assert item["name"].startswith("Рушник на фото")
    assert item["1c_down_presses"] == 3


@pytest.mark.parametrize("name, expected_key", [
    ("Труна Економ", "Труна Е"),
    ("Труна Хадес дитячий 0,8 м", "Труна Х"),
])
def test_two_coffins_at_one_price_are_told_apart_by_name(name, expected_key):
    ok, item, _ = mapped(name, 2700, "coffins")
    assert ok
    assert item["1c_search_key"] == expected_key


# --- отказ вместо неверной догадки ---

def test_partial_name_match_at_a_different_price_is_refused():
    """«Рушник на хрест 380» похоже на «Рушник на хрест 250», но 380 ≠ 250 —
    это другой вариант товара, а не скидка. Лучше ручной ввод."""
    ok, item, warnings = mapped("Рушник на хрест", 380, "crosses")
    assert not ok
    assert "1c_search_key" not in item
    assert any("цена не совпала" in w for w in warnings)


def test_price_only_match_with_an_unrelated_name_is_refused():
    """«Насипний пагорб» за 1200 совпадает по цене с «Перевезення покійного»
    — это совпадение цены, а не смысл. Без alias — только вручную."""
    ok, _item, warnings = mapped("Насипний пагорб", 1200, "services")
    assert not ok
    assert any("aliases" in w for w in warnings)


def test_unknown_category_maps_nothing():
    ok, _item, _ = mapped("Что-то", 100, "нет-такой-категории")
    assert not ok


def test_entry_without_search_key_is_not_used(monkeypatch):
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING", {
        "coffins": [{"name": "Труна Економ", "price": 2700, "search_key": "", "dropdown_index": 0}]
    })
    ok, _, _ = mapped("Труна Економ", 2700, "coffins")
    assert not ok


# --- aliases: способ закрепить связку, которую строками не вывести ---

def test_alias_maps_a_name_that_shares_nothing_with_the_catalog(monkeypatch):
    entries = list(agent_logic.CATALOG_MAPPING["services"])
    entries[0] = {**entries[0], "aliases": ["Послуги персоналу для поховання"]}
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING",
                        {**agent_logic.CATALOG_MAPPING, "services": entries})

    ok, item, _ = mapped("Послуги персоналу для поховання", 6200, "services", qty=4)
    assert ok
    assert item["name"] == "снос (Галстук)"
    assert item["1c_down_presses"] == 9
    assert item["1c_match_reason"] == "alias"


def test_alias_picks_the_variant_matching_the_price(monkeypatch):
    """Один и тот же alias может стоять у нескольких вариантов —
    правильный выбирает цена."""
    entries = [
        {**agent_logic.CATALOG_MAPPING["services"][0], "aliases": ["персонал"]},
        {**agent_logic.CATALOG_MAPPING["services"][1], "aliases": ["персонал"]},
    ]
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING",
                        {**agent_logic.CATALOG_MAPPING, "services": entries})

    _, item, _ = mapped("персонал", 2400, "services")
    assert item["name"] == "снос (Почесний Ескорт)"


# --- маршрутизация по категориям ---

def test_табличка_на_хрест_goes_to_plaques_not_crosses():
    """В названии есть и «табличка», и «хрест». Это табличка — а раньше
    строка уходила в crosses, где табличек нет, и не мапилась никогда."""
    goods = [{"name": "Табличка на хрест", "price": 780, "quantity": 1, "unit_price_for_1c": 780}]
    agent_logic._process_complex_goods_and_mapping(goods, [])
    assert goods[0]["1c_search_key"] == "Табличка н"


# --- нормализация названий услуг ---

def test_compound_cell_keeps_a_real_service_name():
    """Ячейка бланка «Насипний пагорб/Встанов. хреста» раньше схлопывалась
    в «Хрест» — самое короткое совпадение и при этом товар, а не услуга."""
    assert agent_logic.find_best_service_name("Насипний пагорб/Встанов. хреста") == "Насипний пагорб"


def test_abbreviation_still_expands_to_the_full_service():
    assert agent_logic.find_best_service_name("Закопування") == "Закопування/опускання труни"
    assert agent_logic.find_best_service_name("Пронос") == "Пронос труни"


def test_separator_differences_do_not_lose_the_escort():
    """«снос ескорт» через пробел раньше матчилось на короткое «снос»,
    молча теряя надбавку за почётный эскорт."""
    assert agent_logic.find_best_service_name("снос ескорт") == "снос-ескорт"
