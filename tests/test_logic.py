import pytest
from src import agent_logic
from src.validator import validate_and_fix_order, Service

# === ИЗОЛИРУЕМ ТЕСТЫ ОТ РЕАЛЬНОГО КАТАЛОГА ===
agent_logic.CATALOG_MAPPING = {
    "wreaths": [
        {"name": "Вінок новий", "price": 2760},
        {"name": "Вінок новий", "price": 4200},
        {"name": "Вінок новий ГЕРБ", "price": 4550}
    ]
}
agent_logic.KNOWN_UNIT_PRICES = {"Хусточки": [40], "Свічки": [30]}
agent_logic.DIGGING_RULES = {
    "base_price_complete_4_pax": 6400,
    "towel_prices": [1400, 2000]
}


@pytest.fixture
def empty_warnings():
    return []


# --- ТЕСТЫ МАППИНГА И ТОВАРОВ ---

@pytest.mark.parametrize(
    "input_name, input_price, expected_name, expected_presses",
    [
        ("Якийсь вінок від ІІ", 4550, "Вінок новий ГЕРБ", 0),
        ("Венок", 4200, "Вінок новий", 1),
        ("Неизвестный гроб", 999999, "Неизвестный гроб", 0),
        ("", 1000, "", 0),
    ]
)
def test_1c_mapping_scenarios(input_name, input_price, expected_name, expected_presses, empty_warnings):
    goods = [{"name": input_name, "price": input_price, "quantity": 1}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["name"] == expected_name
    assert goods[0]["1c_down_presses"] == expected_presses


def test_goods_math_division(empty_warnings):
    goods = [{"name": "Хусточки", "price": 800, "quantity": 20}]
    agent_logic._process_complex_goods_and_mapping(goods, empty_warnings)
    assert goods[0]["price"] == 40
    assert len(empty_warnings) == 1


# --- ТЕСТЫ ГЛАВНОГО ПАЙПЛАЙНА БИЗНЕС-ЛОГИКИ ---

def test_burial_and_towel_split():
    data = {"services": [{"name": "Закопування/опускання труни", "price": 7800, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])

    assert result["services"][0]["price"] == 6400
    assert result["services"][0]["quantity"] == 1
    assert result["goods"][0]["name"] == "Рушник для опускання"
    assert result["goods"][0]["price"] == 1400


def test_personnel_odd_sum_warning():
    """Проверка безопасного деления и варнинга при нечетной сумме сноса."""
    data = {"services": [{"name": "снос", "price": 1001, "quantity": 2}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])

    assert result["services"][0]["price"] == 500  # 1001 // 2
    assert any("Нечетная сумма" in w for w in result["warnings"])


def test_goods_migration():
    """Проверка переноса товаров из блока услуг на склад."""
    data = {"services": [{"name": "Хусточки носові", "price": 40, "quantity": 20}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])

    assert len(result["services"]) == 0
    assert len(result["goods"]) == 1
    assert result["goods"][0]["name"] == "Хусточки носові"


def test_extra_points_calculation():
    """Проверка правильного начисления доп. точек для авто и людей."""
    data = {
        "services": [
            {"name": "снос", "price": 4000, "quantity": 4},
            {"name": "церемоніймейстер", "price": 1500, "quantity": 1}
        ],
        "transport": [{"name": "Катафалк", "price": 2000, "quantity": 1}]
    }
    # 2 доп точки: 5 человек (4 снос + 1 церемония) * 2 адреса = 10 точек сноса
    # 1 машина * 2 адреса = 2 точки транспорта
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=2, booked_in_1c=[])

    extra_snos = next(s for s in result["services"] if s["name"] == "снос (доп. точка)")
    extra_trans = next(t for t in result["transport"] if t["name"] == "доп. точка (транспорт)")

    assert extra_snos["quantity"] == 10
    assert extra_snos["price"] == 400
    assert extra_trans["quantity"] == 2
    assert extra_trans["price"] == 1000


# --- ТЕСТЫ БЛЭКЛИСТА ---

def test_blacklist_removes_exact_duplicate():
    data = {"services": [{"name": "снос", "price": 1300, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=["снос"])
    assert len(result["services"]) == 0


def test_blacklist_does_not_remove_partial_match():
    data = {"services": [{"name": "снос-ескорт", "price": 2000, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=["снос"])
    assert len(result["services"]) == 1


# --- ТЕСТЫ ВАЛИДАТОРА (PYDANTIC) ---

def test_validator_calculates_total_correctly():
    raw_order = {
        "deceased": {"fio": "Тест"},
        "customer": {"fio": "Заказчик"},
        "services": [{"name": "Копка", "price": 1000, "quantity": 2}],
        "goods": [{"name": "Гроб", "price": 5000, "quantity": 1}],
        "transport": [{"name": "Катафалк", "price": 2000, "quantity": 1}],
        "handwritten_total": 99999
    }
    clean_data = validate_and_fix_order(raw_order)
    assert clean_data["calculated_total"] == 9000
    assert clean_data["handwritten_total"] == 99999


def test_service_preserves_1c_down_presses():
    s = Service(**{"name": "Труна", "price": 5000, "quantity": 1, "1c_down_presses": 3})
    dumped = s.model_dump(by_alias=True)
    assert dumped["1c_down_presses"] == 3