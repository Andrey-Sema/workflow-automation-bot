"""Tests for catalog.json validation.

The interesting cases here are the *semantic* ones. A catalog that parses
fine but is ambiguous doesn't fail at runtime — it makes the bot type a
confidently wrong nomenclature line into 1C, and 1C entry has no undo. So
each of these mirrors a real conflict found in the production catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.catalog_schema import validate_catalog, validate_catalog_file

SAMPLE_CATALOG = Path(__file__).resolve().parent.parent / "data" / "catalog.sample.json"


def codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def minimal(**overrides) -> dict:
    base = {
        "tariffs": {"extra_point": 500},
        "digging_rules": {"kopka_person_count": 4, "base_price_per_person": 1600, "towel_prices": [1400]},
        "known_unit_prices": {},
        "personnel_packages": {},
        "catalog_1c_mapping": {},
        "services_list": ["Катафалк"],
    }
    base.update(overrides)
    return base


# --- структурные ошибки ---

def test_empty_catalog_is_an_error():
    assert not validate_catalog({}).ok


def test_non_object_catalog_is_an_error():
    report = validate_catalog([1, 2, 3])
    assert not report.ok
    assert "NOT_AN_OBJECT" in codes(report)


def test_price_as_string_is_rejected():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "coffins": [{"name": "Труна", "price": "не число", "search_key": "Труна"}]
    }))
    assert not report.ok


def test_negative_price_is_rejected():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "coffins": [{"name": "Труна", "price": -100, "search_key": "Труна"}]
    }))
    assert not report.ok


def test_one_broken_section_does_not_discard_the_good_ones():
    """Частично сломанный каталог должен остаться рабочим в уцелевшей части —
    иначе одна опечатка в цене останавливает бота целиком."""
    report = validate_catalog(minimal(
        services_list=["Катафалк", "Пронос труни"],
        catalog_1c_mapping={"coffins": [{"name": "Труна", "price": "мусор"}]},
    ))
    assert not report.ok
    assert report.stats["services_list"] == 2


# --- семантические конфликты ---

def test_duplicate_price_in_one_category_is_flagged():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "towels": [
            {"name": "Рушник на фото 350", "price": 350, "search_key": "Рушник н", "dropdown_index": 0},
            {"name": "Отче 350", "price": 350, "search_key": "Отче", "dropdown_index": 1},
        ]
    }))
    assert report.ok  # не ошибка, но требует внимания
    assert "DUPLICATE_PRICE" in codes(report)


def test_same_dropdown_slot_claimed_by_two_items_is_flagged():
    """В 1С «search_key + N нажатий вниз» — это одна строка списка.
    Две разные позиции на одной строке означают, что одна из них неверна."""
    report = validate_catalog(minimal(catalog_1c_mapping={
        "towels": [{"name": "Рушник на хрест 250", "price": 250, "search_key": "Рушник н", "dropdown_index": 1}],
        "crosses": [{"name": "Рушник на хрест 350", "price": 350, "search_key": "Рушник н", "dropdown_index": 1}],
    }))
    assert "DROPDOWN_COLLISION" in codes(report)


def test_blank_search_key_is_flagged():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "coffins": [{"name": "Труна Економ", "price": 2700, "search_key": ""}]
    }))
    assert "BLANK_SEARCH_KEY" in codes(report)


def test_category_the_code_cannot_route_is_flagged():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "candles": [{"name": "Свічка", "price": 30, "search_key": "Свічка"}]
    }))
    assert "UNKNOWN_CATEGORY" in codes(report)


def test_personnel_package_price_must_match_a_tariff():
    report = validate_catalog(minimal(
        tariffs={"snos_base": 1550},
        personnel_packages={"6200": {"name": "снос", "qty": 4}, "9999": {"name": "снос", "qty": 3}},
    ))
    assert "PACKAGE_TARIFF" in codes(report)


def test_personnel_package_that_does_not_divide_evenly_is_flagged():
    report = validate_catalog(minimal(personnel_packages={"1000": {"name": "снос", "qty": 3}}))
    assert "PACKAGE_MATH" in codes(report)


def test_towel_price_without_a_catalog_item_is_flagged():
    report = validate_catalog(minimal(
        digging_rules={"kopka_person_count": 4, "base_price_per_person": 1600, "towel_prices": [1400]},
        catalog_1c_mapping={"towels": [{"name": "Рушник Льон", "price": 70, "search_key": "Рушник Л"}]},
    ))
    assert "TOWEL_UNMAPPED" in codes(report)


def test_known_unit_price_without_a_1c_item_is_flagged():
    report = validate_catalog(minimal(
        known_unit_prices={"Хусточки": [40]},
        catalog_1c_mapping={"towels": [{"name": "Рушник Льон", "price": 70, "search_key": "Рушник Л"}]},
    ))
    assert "KNOWN_PRICE_UNMAPPED" in codes(report)


def test_cross_reference_checks_stay_quiet_without_a_mapping_section():
    """Каталог без catalog_1c_mapping: жаловаться на каждую позицию, что ей
    нет соответствия, бессмысленно — отсутствует сама секция."""
    report = validate_catalog(minimal(
        known_unit_prices={"Хусточки": [40]},
        digging_rules={"kopka_person_count": 4, "base_price_per_person": 1600, "towel_prices": [1400]},
    ))
    assert not report.warnings


def test_aliases_are_accepted():
    report = validate_catalog(minimal(catalog_1c_mapping={
        "services": [{
            "name": "снос (Галстук)", "price": 1550, "search_key": "снос",
            "dropdown_index": 9, "aliases": ["Послуги персоналу для поховання"],
        }]
    }))
    assert report.ok


# --- файловый уровень ---

def test_missing_file_reports_not_found(tmp_path):
    report = validate_catalog_file(tmp_path / "нет-такого.json")
    assert not report.ok
    assert "NOT_FOUND" in codes(report)


def test_malformed_json_reports_bad_json(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{это не json", encoding="utf-8")
    report = validate_catalog_file(path)
    assert not report.ok
    assert "BAD_JSON" in codes(report)


# --- реальный каталог ---

@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="нет data/catalog.sample.json")
def test_shipped_sample_catalog_is_structurally_valid():
    """Каталог из репозитория обязан грузиться без ошибок структуры.
    Предупреждения допустимы — это реальные неоднозначности прайса."""
    report = validate_catalog_file(SAMPLE_CATALOG)
    assert report.ok, report.as_text()
    assert report.stats["mapping_items"] > 200
    assert report.stats["services_list"] > 50


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="нет data/catalog.sample.json")
def test_shipped_sample_catalog_matches_documented_conflicts():
    """Фиксируем известные конфликты боевого каталога: если их станет
    больше — кто-то добавил новую неоднозначность и её надо разобрать."""
    report = validate_catalog_file(SAMPLE_CATALOG)
    found = codes(report)
    assert "DUPLICATE_PRICE" in found
    assert "DROPDOWN_COLLISION" in found
    assert len(report.warnings) <= 12, report.as_text()


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="нет data/catalog.sample.json")
def test_sample_catalog_is_valid_json_with_expected_sections():
    data = json.loads(SAMPLE_CATALOG.read_text(encoding="utf-8"))
    for section in ("tariffs", "digging_rules", "personnel_packages",
                    "known_unit_prices", "catalog_1c_mapping", "services_list"):
        assert section in data, f"в образце каталога нет секции {section}"
