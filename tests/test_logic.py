from unittest.mock import MagicMock, patch

import pytest

from src import agent_logic
from src.validator import validate_and_fix_order


# === ИЗОЛИРУЕМ ТЕСТЫ ОТ РЕАЛЬНОГО КАТАЛОГА ===
@pytest.fixture(autouse=True)
def mock_catalog(monkeypatch):
    monkeypatch.setattr(agent_logic, "CATALOG_MAPPING", {
        "wreaths": [
            {"name": "Вінок новий", "price": 2760, "search_key": "Вінок н", "dropdown_index": 0},
            {"name": "Вінок новий ГЕРБ", "price": 4550, "search_key": "Вінок н", "dropdown_index": 1}
            # Исправлен пропуск индекса
        ]
    })
    monkeypatch.setattr(agent_logic, "KNOWN_UNIT_PRICES", {"Хусточки": [40], "Свічки": [30]})
    monkeypatch.setattr(agent_logic, "DIGGING_RULES", {
        "kopka_person_count": 4,
        "base_price_per_person": 1600,
        "towel_prices": [1400, 2000]
    })
    monkeypatch.setattr(agent_logic, "TARIFFS", {
        "extra_point": 500,
        "transport_base": 1000
    })
    monkeypatch.setattr(agent_logic, "SERVICES_LIST", [
        "Катафалк", "Пронос труни", "Послуги священика", "Закопування/опускання труни", "снос"
    ])
    # find_best_service_name — lru_cache'ится по одному лишь raw_name, поэтому
    # без сброса между тестами результат может утечь из другого набора SERVICES_LIST.
    agent_logic.find_best_service_name.cache_clear()


# --- ТЕСТЫ МАППИНГА И ТОВАРОВ ---

@pytest.mark.parametrize(
    "input_name, input_price, expected_name, expected_presses, expected_unit_price",
    [
        ("Якийсь вінок від ІІ", 4550, "Вінок новий ГЕРБ", 1, 4550),  # Индекс исправлен
        ("Неизвестный гроб", 999999, "Неизвестный гроб", 0, 999999),
        ("", 1000, "", 0, 1000),
    ]
)
def test_1c_mapping_scenarios(input_name, input_price, expected_name, expected_presses, expected_unit_price):
    goods = [{"name": input_name, "price": input_price, "quantity": 1, "unit_price_for_1c": input_price}]
    agent_logic._process_complex_goods_and_mapping(goods, [])  # Инлайн пустого списка warnings

    assert goods[0]["name"] == expected_name
    assert goods[0]["1c_down_presses"] == expected_presses
    assert goods[0]["unit_price_for_1c"] == expected_unit_price  # Добавлена проверка unit_price


def test_goods_math_healing():
    """Проверка исправления ошибки ИИ: бот прислал 1 пачку за 800 грн."""
    goods = [{"name": "Хусточки", "price": 800, "quantity": 1}]
    agent_logic._process_complex_goods_and_mapping(goods, [])

    assert goods[0]["quantity"] == 20  # 800 / 40
    assert goods[0]["unit_price_for_1c"] == 40
    assert goods[0]["price"] == 800  # Итоговая сумма не должна меняться


def test_fuzzy_matching():
    """Проверка нечеткого поиска по словарю услуг."""
    agent_logic.find_best_service_name.cache_clear()
    assert agent_logic.find_best_service_name("Ктафалк") == "Катафалк"
    assert agent_logic.find_best_service_name("Пронос") == "Пронос труни"
    assert agent_logic.find_best_service_name("Абсолютно левая дичь") == "Абсолютно левая дичь"


def test_substring_matching_handles_abbreviations():
    """Регресс: "Закопування" должно матчиться на "Закопування/опускання труни"
    (раньше падало ниже порога difflib 0.6 и оставалось нераспознанным)."""
    agent_logic.find_best_service_name.cache_clear()
    assert agent_logic.find_best_service_name("Закопування") == "Закопування/опускання труни"
    assert agent_logic.find_best_service_name("закопування") == "Закопування/опускання труни"


def test_exact_match_is_case_insensitive():
    agent_logic.find_best_service_name.cache_clear()
    assert agent_logic.find_best_service_name("катафалк") == "Катафалк"


# --- ТЕСТЫ ГЛАВНОГО ПАЙПЛАЙНА БИЗНЕС-ЛОГИКИ ---

def test_burial_and_towel_split():
    data = {"services": [{"name": "Закопування", "price": 7800, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])

    assert result["services"][0]["price"] == 6400  # 1600 * 4
    assert result["services"][0]["quantity"] == 1
    assert result["services"][0]["name"] == "Закопування/опускання труни"
    assert result["goods"][0]["name"] == "Рушник для опускання"
    assert result["goods"][0]["price"] == 1400


def test_personnel_unit_price_calculation():
    data = {"services": [{"name": "снос", "price": 1001, "quantity": 2}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])
    assert result["services"][0]["unit_price_for_1c"] == 500  # 1001 // 2


def test_extra_points_calculation():
    data = {
        "services": [{"name": "снос", "price": 4000, "quantity": 4}],
        "transport": [{"name": "Катафалк", "price": 2000, "quantity": 1}]
    }
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=2, booked_in_1c=[])

    extra_snos = next(s for s in result["services"] if "доп. точка" in s["name"])
    extra_trans = next(t for t in result["transport"] if "доп. точка" in t["name"])

    assert extra_snos["quantity"] == 8  # 4 * 2 адреса
    assert extra_snos["price"] == 4000  # 8 * 500
    assert extra_trans["quantity"] == 2
    assert extra_trans["price"] == 2000  # 2 * 1000


def test_extra_points_boundary_values():
    """Тест на граничные значения: при 0 адресов доп. точки не начисляются."""
    data = {"services": [{"name": "снос", "price": 4000, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=[])
    assert not any("доп. точка" in s.get("name", "") for s in result["services"])


def test_blacklist_logic():
    data = {"services": [{"name": "снос", "price": 1300, "quantity": 4}]}
    result = agent_logic.apply_business_rules_in_python(data, num_addresses=0, booked_in_1c=["снос"])
    assert len(result["services"]) == 0


# --- ТЕСТЫ API (MOCKING) ---

@patch("src.agent_logic.get_client")
def test_validate_and_normalize_api_call(mock_get_client):
    """Тест пайплайна без интернета (перехват API нового SDK)."""
    mock_response = MagicMock()
    mock_response.text = '{"services": [{"name": "Снос", "price": 1000, "quantity": 1}]}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    result = agent_logic.validate_and_normalize('Снос - 1000', num_addresses=0, booked_in_1c=[])

    mock_client.models.generate_content.assert_called_once()
    assert "services" in result
    assert len(result["services"]) == 1


def test_validate_and_normalize_prompt_includes_catalog_data():
    """Регресс: раньше SERVICES_JSON/CEMETERIES_JSON не были определены в
    agent_logic и промпт падал с NameError при каждом вызове."""
    with patch("src.agent_logic.get_client") as mock_get_client:
        mock_response = MagicMock()
        mock_response.text = '{"services": []}'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        agent_logic.validate_and_normalize('Снос - 1000', num_addresses=0, booked_in_1c=[])

        _, kwargs = mock_client.models.generate_content.call_args
        assert "SERVICES DICTIONARY" in kwargs["contents"]
        assert kwargs["config"].response_mime_type == "application/json"


def test_validate_and_normalize_fails_fast_when_circuit_breaker_open():
    from src.circuit_breaker import gemini_circuit_breaker
    from src.errors import GeminiUnavailableError

    for _ in range(10):
        gemini_circuit_breaker.record_failure()

    with patch("src.agent_logic.get_client") as mock_get_client, pytest.raises(GeminiUnavailableError):
        agent_logic.validate_and_normalize("Снос - 1000", num_addresses=0, booked_in_1c=[])

    mock_get_client.assert_not_called()


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
    assert clean_data["calculated_total"] == 8000
    assert clean_data["handwritten_total"] == 99999


def test_validator_fixes_negative_prices_by_excluding_them():
    """Если пришла отрицательная цена, Pydantic должен откинуть мусорную позицию, но спасти наряд."""
    raw_order = {
        "deceased": {"fio": "Тест"},
        "customer": {},
        "services": [{"name": "Снос", "price": -500, "quantity": 1}],
        "goods": [{"name": "Гроб", "price": 5000, "quantity": 1}]
    }
    clean_data = validate_and_fix_order(raw_order)

    assert len(clean_data["services"]) == 0
    assert len(clean_data["goods"]) == 1
    assert clean_data["calculated_total"] == 5000
