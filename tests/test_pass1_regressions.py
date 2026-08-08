"""Регрессии, найденные при сплошном прогоне проекта (прогон 1).

Каждый тест соответствует багу, который прошёл мимо линтеров, bandit и
существующих тестов — то есть ловится только сценарием на реальных данных.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src import agent_logic, catalog_admin, config
from src.catalog_schema import sanitize_catalog
from src.errors import CatalogLoadError
from src.order_conflicts import build_order_summary
from src.utils import MAX_PG_INT, fix_temporal_hallucinations
from src.validator import validate_and_fix_order

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.sample.json"


# ── BUG-1: «щит от галлюцинаций» переписывал настоящие даты ──────────────

def test_death_in_a_previous_year_is_kept():
    """На реальном наряде: смерть 22.12.2024, похороны 08.08.2026
    (перезахоронение/долгое хранение тела). Год смерти подтягивался к
    текущему, и получалась смерть ПОСЛЕ похорон — она же уезжала в 1С."""
    last_year = datetime.now().year - 2
    assert fix_temporal_hallucinations(f"22.12.{last_year}") == f"22.12.{last_year}"


@pytest.mark.parametrize("years_ago", [0, 1, 3, 9])
def test_plausible_past_dates_are_never_rewritten(years_ago):
    d = datetime.now().replace(month=6, day=15) - timedelta(days=365 * years_ago)
    text = d.strftime("%d.%m.%Y")
    assert fix_temporal_hallucinations(text) == text


@pytest.mark.parametrize("date_str", ["01.01.1905", "31.12.2140"])
def test_impossible_years_are_still_corrected(date_str):
    """Сузили правило, но не отключили: 1905 и 2140 — точно не бланк."""
    assert fix_temporal_hallucinations(date_str).endswith(str(datetime.now().year))


def test_day_month_without_year_still_gets_current_year():
    assert fix_temporal_hallucinations("26.03").endswith(str(datetime.now().year))


def test_death_after_burial_is_reported_as_a_conflict():
    """Раньше это состояние было невозможно заметить: его же и создавал
    «щит», молча подтягивая год смерти."""
    summary = build_order_summary(validate_and_fix_order({
        "deceased": {"fio": "Т", "death_date": "22.12.2026", "burial_date": "08.08.2026"},
        "customer": {},
    }))
    assert summary.death_after_burial
    assert summary.has_conflicts


def test_ordinary_date_pair_raises_no_conflict():
    summary = build_order_summary({
        "deceased": {"death_date": "30.07.2026", "burial_date": "09.08.2026"}, "customer": {},
    })
    assert not summary.death_after_burial


@pytest.mark.parametrize("deceased", [
    {}, {"death_date": "30.07.2026"}, {"burial_date": "09.08.2026"},
    {"death_date": "не дата", "burial_date": "09.08.2026"},
    {"death_date": None, "burial_date": None},
])
def test_incomplete_dates_do_not_produce_a_false_conflict(deceased):
    assert not build_order_summary({"deceased": deceased, "customer": {}}).death_after_burial


# ── BUG-2: битый каталог ронял пайплайн посреди наряда ──────────────────

@pytest.mark.parametrize("mapping", [
    {"coffins": [{"price": 1550, "search_key": "Т"}]},          # нет name
    {"coffins": ["строка вместо объекта"]},
    {"coffins": {"name": "Труна"}},                              # категория не список
    {"coffins": [{"name": "Труна", "price": -5, "search_key": "Т"}]},
    {"coffins": [{"name": "  ", "price": 10, "search_key": "Т"}]},
])
def test_broken_catalog_entries_are_dropped_not_crashed(mapping, monkeypatch):
    """Валидатор о них сообщал, но config публиковал сырые данные, и
    agent_logic падал на KeyError/AttributeError уже во время обработки."""
    monkeypatch.setattr(config, "CATALOG_DATA", sanitize_catalog(
        {"catalog_1c_mapping": mapping, "services_list": ["Труна"]}
    ))
    agent_logic.refresh_from_catalog()

    result = agent_logic.apply_business_rules_in_python(
        {"goods": [{"name": "Труна", "price": 1550, "quantity": 1}]}, 0, []
    )
    assert result["calculated_total"] == 1550


def test_mapping_section_that_is_not_an_object_is_dropped():
    assert sanitize_catalog({"catalog_1c_mapping": "мусор"}).get("catalog_1c_mapping") is None


def test_sanitize_keeps_valid_entries_alongside_broken_ones():
    clean = sanitize_catalog({"catalog_1c_mapping": {"coffins": [
        {"name": "Труна Економ", "price": 2700, "search_key": "Труна Е"},
        {"price": 1, "search_key": "нет имени"},
    ]}})
    assert [e["name"] for e in clean["catalog_1c_mapping"]["coffins"]] == ["Труна Економ"]


def test_sanitize_does_not_mutate_its_input():
    original = {"catalog_1c_mapping": {"coffins": [{"price": 1}]}}
    snapshot = copy.deepcopy(original)
    sanitize_catalog(original)
    assert original == snapshot


@pytest.mark.skipif(not CATALOG.exists(), reason="нет каталога")
def test_real_catalog_loses_nothing_to_sanitization():
    """Боевой каталог валиден — очистка не должна выбросить ни одной позиции."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    before = sum(len(v) for v in data["catalog_1c_mapping"].values())
    after = sum(len(v) for v in sanitize_catalog(data)["catalog_1c_mapping"].values())
    assert after == before


# ── BUG-2b: /set_tariff мог стереть отброшенные позиции из файла ────────

def test_tariff_edit_preserves_entries_that_sanitization_dropped(tmp_path, monkeypatch):
    """В памяти каталог очищенный. Если писать на диск его, то правка
    тарифа молча удаляла бы из прайса позиции, которые оператор ещё не
    починил."""
    path = tmp_path / "catalog.json"
    on_disk = {
        "tariffs": {"extra_point": 500},
        "services_list": ["Катафалк"],
        "catalog_1c_mapping": {"coffins": [
            {"name": "Труна Економ", "price": 2700, "search_key": "Труна Е"},
            {"price": 1, "search_key": "битая, но своя"},
        ]},
    }
    path.write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config._settings, "data_dir", tmp_path)
    monkeypatch.setattr(config, "CATALOG_DATA", sanitize_catalog(on_disk))

    catalog_admin.update_tariff("extra_point", 600)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["tariffs"]["extra_point"] == 600
    assert len(written["catalog_1c_mapping"]["coffins"]) == 2, "битая позиция пропала из файла"


def test_tariff_edit_reports_an_unreadable_file(tmp_path, monkeypatch):
    (tmp_path / "catalog.json").write_text("{битый", encoding="utf-8")
    monkeypatch.setattr(config._settings, "data_dir", tmp_path)
    monkeypatch.setattr(config, "CATALOG_DATA", {"tariffs": {"extra_point": 500}})

    with pytest.raises(CatalogLoadError):
        catalog_admin.update_tariff("extra_point", 600)


# ── BUG-3: правила мутировали входной словарь ──────────────────────────

@pytest.mark.skipif(not CATALOG.exists(), reason="нет каталога")
def test_business_rules_do_not_mutate_their_input():
    config.load_catalog_from(CATALOG)
    agent_logic.refresh_from_catalog()
    raw = json.loads((ROOT / "tests" / "fixtures" / "order_base.json").read_text(encoding="utf-8"))
    raw.pop("_meta", None)
    snapshot = copy.deepcopy(raw)

    agent_logic.apply_business_rules_in_python(raw, 2, [])

    assert raw == snapshot, "функция переименовала/переоценила позиции у вызывающего"


@pytest.mark.skipif(not CATALOG.exists(), reason="нет каталога")
def test_running_the_rules_twice_gives_the_same_order():
    config.load_catalog_from(CATALOG)
    agent_logic.refresh_from_catalog()
    raw = json.loads((ROOT / "tests" / "fixtures" / "order_vip.json").read_text(encoding="utf-8"))
    raw.pop("_meta", None)

    first = agent_logic.apply_business_rules_in_python(raw, 2, [])
    second = agent_logic.apply_business_rules_in_python(raw, 2, [])
    assert first == second


# ── BUG-4: цена не имела верхней границы ───────────────────────────────

def test_absurd_price_drops_the_line_and_keeps_the_order():
    order = validate_and_fix_order({"deceased": {}, "customer": {}, "goods": [
        {"name": "Труна", "price": 10 ** 15, "quantity": 1},
        {"name": "Хрест", "price": 1710, "quantity": 1},
    ]})
    assert [g["name"] for g in order["goods"]] == ["Хрест"]
    assert order["calculated_total"] == 1710


def test_price_at_the_limit_is_still_accepted():
    order = validate_and_fix_order({"deceased": {}, "customer": {}, "goods": [
        {"name": "Труна", "price": MAX_PG_INT, "quantity": 1},
    ]})
    assert order["calculated_total"] == MAX_PG_INT


# ── прогон 2: найдено при повторном сплошном прогоне ────────────────────

# BUG-5: /set_tariff принимал отрицательные/нулевые значения без всякой
# проверки — опечатка администратора (лишний минус, недостающая цифра)
# молча портила математику каждого следующего наряда. kopka_person_count=0
# особенно опасен: base_burial_price становится 0, и правило S2 (деление
# закопки/рушника) начинает срабатывать почти на любой сумме закопки.

@pytest.fixture
def tariff_catalog(tmp_path, monkeypatch):
    initial = {
        "tariffs": {"extra_point": 500, "transport_base": 1000},
        "digging_rules": {"base_price_per_person": 1925, "kopka_person_count": 4},
        "services_list": [],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config._settings, "data_dir", tmp_path)
    monkeypatch.setattr(config, "CATALOG_DATA", dict(initial))
    yield path


@pytest.mark.parametrize("key, value", [
    ("extra_point", -500),
    ("transport_base", -1),
    ("base_price_per_person", -1925),
    ("kopka_person_count", 0),
    ("kopka_person_count", -4),
])
def test_negative_or_zero_tariff_is_rejected(tariff_catalog, key, value):
    with pytest.raises(ValueError, match=str(value) if value < 0 else "0"):
        catalog_admin.update_tariff(key, value)
    # Файл не должен быть тронут отклонённой правкой.
    on_disk = json.loads(tariff_catalog.read_text(encoding="utf-8"))
    section = "tariffs" if key in catalog_admin.EDITABLE_TARIFFS else "digging_rules"
    assert on_disk[section].get(key) != value


def test_zero_extra_point_is_a_legitimate_value(tariff_catalog):
    """0 — «доп. точки бесплатно», это не опечатка. Запрещать нельзя."""
    catalog_admin.update_tariff("extra_point", 0)
    assert config.CATALOG_DATA["tariffs"]["extra_point"] == 0


def test_absurdly_large_tariff_is_rejected(tariff_catalog):
    with pytest.raises(ValueError):
        catalog_admin.update_tariff("extra_point", 10**15)


# BUG-6: apply_business_rules_in_python типизирована как `booked_in_1c:
# list[str]`, но публична — прямой вызов с None/нестроковыми элементами
# падал вместо того, чтобы просто не находить совпадений.

@pytest.mark.parametrize("booked", [None, [123], [{"name": "снос"}], ["", None]])
def test_malformed_booked_list_does_not_crash_the_pipeline(booked):
    result = agent_logic.apply_business_rules_in_python(
        {"services": [{"name": "снос", "price": 1550, "quantity": 1}]}, 0, booked
    )
    assert result["calculated_total"] == 1550


def test_well_formed_booked_list_still_deduplicates():
    result = agent_logic.apply_business_rules_in_python(
        {"services": [{"name": "снос", "price": 1550, "quantity": 1}]}, 0, ["снос"]
    )
    assert result["services"] == []
    assert result["calculated_total"] == 0
