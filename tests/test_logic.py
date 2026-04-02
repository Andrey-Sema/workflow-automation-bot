import pytest
from src import agent_logic
from src.validator import validate_and_fix_order

# === ИЗОЛИРУЕМ ТЕСТЫ ОТ РЕАЛЬНОГО КАТАЛОГА ===
# Подменяем базу данных в памяти на время тестов!
agent_logic.CATALOG_MAPPING = {
    "wreaths": [
        {"name": "Вінок новий", "price": 2760},
        {"name": "Вінок новий", "price": 4200},
        {"name": "Вінок новий ГЕРБ", "price": 4550}
    ]
}
agent_logic.KNOWN_UNIT_PRICES = {"Хусточки": [40], "Свічки": [30]}
agent_logic.DIGGING_RULES = {"base_price_per_person": 1600, "towel_prices": [1400, 2000]}

@pytest.fixture
def empty_warnings():
    return []

@pytest.mark.parametrize(
    "input_name, input_price, expected_name, expected_presses",
    [
        ("Якийсь вінок від ІІ", 4550, "Вінок новий ГЕРБ", 0),
        ("Венок", 4200, "Вінок новий", 1),
        ("Неизвестный гроб", 999999, "Неизвестный гроб", 0),
        ("", 1000, "", 0),
        (None, 1000, "", 0),
    ]
)
def test_1c_mapping_scenarios(input_name, input_price, expected_name, expected_presses, empty_warnings):
    safe_name = input_name if input_name is not None else ""
    goods = [{"name": safe_name, "price": input_price, "quantity": 1}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["name"] == expected_name
    assert goods[0]["1c_down_presses"] == expected_presses

def test_goods_math_division(empty_warnings):
    goods = [{"name": "Хусточки", "price": 800, "quantity": 20}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["price"] == 40
    assert goods[0]["quantity"] == 20
    assert len(empty_warnings) == 1

def test_goods_math_already_correct(empty_warnings):
    goods = [{"name": "Свічки", "price": 30, "quantity": 20}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["price"] == 30
    assert goods[0]["quantity"] == 20
    assert len(empty_warnings) == 0

def test_goods_zero_quantity_protection(empty_warnings):
    goods = [{"name": "Хусточки", "price": 800, "quantity": 0}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["price"] == 800
    assert goods[0]["quantity"] == 0

@pytest.mark.parametrize(
    "raw_price, raw_qty, expected_person_qty, expected_towel_price",
    [
        (7800, 4, 4, 1400),
        (11000, 1, 6, 1400),
        (3200, 1, 2, 0),
    ]
)
def test_digging_logic_scenarios(raw_price, raw_qty, expected_person_qty, expected_towel_price):
    services = [{"name": "Закопування/опускання труни", "price": raw_price, "quantity": raw_qty}]
    new_srv, warnings = agent_logic._process_digging_logic(services)
    assert services[0]["price"] == 1600
    assert services[0]["quantity"] == expected_person_qty
    if expected_towel_price > 0:
        assert new_srv[0]["price"] == expected_towel_price

def test_validator_keeps_all_categories_and_calculates_total():
    raw_order = {
        "deceased": {"fio": "Тест Тестович"},
        "customer": {"fio": "Заказчик"},
        "services": [{"name": "Копка", "price": 1000, "quantity": 2}],
        "goods": [{"name": "Гроб", "price": 5000, "quantity": 1}],
        "transport": [{"name": "Катафалк", "price": 2000, "quantity": 1}],
        "handwritten_total": 9000
    }
    clean_data = validate_and_fix_order(raw_order)
    assert clean_data["calculated_total"] == 9000
    assert clean_data["handwritten_total"] == 9000