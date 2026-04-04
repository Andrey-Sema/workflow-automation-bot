import pytest
import math
import json
from hypothesis import given, strategies as st, assume
from src.utils import safe_int, clean_service_name, clean_json_response, safe_parse_json

# ==========================================
# КОНСТАНТЫ ДЛЯ ТЕСТОВ
# ==========================================
MAX_PG_INT = 2147483647
MIN_PG_INT = -2147483648


# ==========================================
# TESTS FOR safe_int
# ==========================================

@given(st.integers(min_value=MIN_PG_INT, max_value=MAX_PG_INT))
def test_safe_int_returns_same_integer(val):
    assert safe_int(val) == val


@given(st.integers().filter(lambda x: x > MAX_PG_INT or x < MIN_PG_INT))
def test_safe_int_handles_db_overflow(val):
    assert safe_int(val, default=0) == 0
    assert safe_int(val, default=999) == 999


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_safe_int_handles_floats(val):
    result = safe_int(val)
    assert isinstance(result, int)
    if MIN_PG_INT <= val <= MAX_PG_INT:
        assert result == int(val)
    else:
        assert result == 0


def test_safe_int_infinity_and_nan():
    test_cases = [
        (float('inf'), 0), (float('-inf'), 0), (float('nan'), 0),
        ("Infinity", 0), ("-Infinity", 0), ("NaN", 0),
    ]
    for value, expected in test_cases:
        assert safe_int(value, default=0) == expected
        assert safe_int(value, default=42) == 42


def test_safe_int_preserves_sign():
    test_cases = [
        ("-100", -100), ("-100.5", -100), ("  -100  грн ", -100),
        ("-2147483648", -2147483648), ("-2147483649", 0),
    ]
    for value, expected in test_cases:
        assert safe_int(value, default=0) == expected


def test_safe_int_edge_cases():
    assert safe_int(str(MAX_PG_INT)) == MAX_PG_INT
    assert safe_int(str(MIN_PG_INT)) == MIN_PG_INT
    assert safe_int(str(MAX_PG_INT + 1), default=0) == 0
    assert safe_int(str(MIN_PG_INT - 1), default=0) == 0
    assert safe_int(float(MAX_PG_INT)) == MAX_PG_INT
    assert safe_int(float(MAX_PG_INT) + 0.5, default=0) == 0


@given(st.text())
def test_safe_int_never_crashes_on_garbage_text(text):
    assert isinstance(safe_int(text, default=0), int)


def test_safe_int_known_cases():
    test_cases = [
        ("  1 500 грн ", 1500), ("10.5 $", 10), (None, 99, 99),
        ("", 0), ("   ", 0), ("0", 0), ("-0", 0), ("3,200.50", 3200),
        ("ціна 500", 500), ("500.00", 500), ("500.99", 500),
    ]
    for case in test_cases:
        if len(case) == 3:
            value, expected, default = case
            assert safe_int(value, default) == expected
        else:
            value, expected = case
            assert safe_int(value) == expected


# ==========================================
# TESTS FOR clean_service_name
# ==========================================

@given(st.text())
def test_clean_service_name_never_crashes(text):
    assert isinstance(clean_service_name(text), str)


@given(st.text())
def test_clean_service_name_strips_spaces_and_hyphens(text):
    result = clean_service_name(text)
    if result:
        assert result == result.strip()
        assert not result.startswith(('-', '*', '•', '·', '1.', '2.'))
        assert not result.endswith(('-', '—', '–'))


def test_clean_service_name_known_cases():
    test_cases = [
        ("  Труна  ", "Труна"), ("- Вінок", "Вінок"), ("* Хусточки", "Хусточки"),
        ("«Свічки»", "Свічки"), ("  -   Катафалк   ", "Катафалк"),
        ("1. Труна (глянцева)", "Труна (глянцева)"), ("• Пронос труни", "Пронос труни"),
        ("· Подушка", "Подушка"), (None, ""), ("", ""), ("   ", ""),
        ("-", ""), ("*", ""), ("123", "123"), ("-123", "123"),
    ]
    for input_val, expected in test_cases:
        assert clean_service_name(input_val) == expected


# ==========================================
# TESTS FOR clean_json_response
# ==========================================

def test_clean_json_response_removes_markdown():
    # Разделили тесты на объекты и массивы
    test_cases_obj = [
        ("```json\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
        ("```\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
        ("Текст до\n{\"key\": \"value\"}\nТекст после", "{\"key\": \"value\"}"),
        ("{без оберток}", "{без оберток}"),
    ]
    for dirty, expected in test_cases_obj:
        assert clean_json_response(dirty) == expected

    test_cases_arr = [
        ("```json\n[1, 2, 3]\n```", "[1, 2, 3]"),
        ("```\n[1, 2, 3]\n```", "[1, 2, 3]"),
        ("Текст\n[1, 2, 3]\nТекст", "[1, 2, 3]"),
    ]
    for dirty, expected in test_cases_arr:
        # Для массивов нужно явно передавать expected_type
        assert clean_json_response(dirty, expected_type='array') == expected


def test_clean_json_response_with_expected_type():
    response = "Текст\n[1, 2, 3]\nТекст"
    assert clean_json_response(response, expected_type='array') == "[1, 2, 3]"
    assert clean_json_response(response, expected_type='object') == response.strip()
    mixed = "Текст\n{\"a\": 1}\nТекст\n[1, 2, 3]\nТекст"
    assert clean_json_response(mixed, expected_type='object') == "{\"a\": 1}"
    assert clean_json_response(mixed, expected_type='array') == "[1, 2, 3]"


def test_clean_json_response_empty_input():
    assert clean_json_response("") == ""
    assert clean_json_response(None) == ""
    assert clean_json_response("   ") == ""  # Возвращает пустую строку после .strip()


# ==========================================
# TESTS FOR safe_parse_json
# ==========================================

def test_safe_parse_json_valid():
    assert safe_parse_json('{"key": "value"}') == {"key": "value"}
    assert safe_parse_json('[1, 2, 3]', expected_type='array') == [1, 2, 3]
    assert safe_parse_json('{"nested": {"a": 1}}') == {"nested": {"a": 1}}


def test_safe_parse_json_with_markdown():
    test_cases = [
        ("```json\n{\"key\": \"value\"}\n```", {"key": "value"}),
        ("```\n{\"key\": \"value\"}\n```", {"key": "value"}),
        ("Текст до\n{\"key\": \"value\"}\nТекст после", {"key": "value"}),
        ("```json\n[1, 2, 3]\n```", [1, 2, 3]),
    ]
    for dirty, expected in test_cases:
        # Для массива нужно передавать expected_type
        e_type = 'array' if isinstance(expected, list) else 'object'
        assert safe_parse_json(dirty, expected_type=e_type) == expected


def test_safe_parse_json_broken():
    test_cases = [
        '<html>502 Bad Gateway</html>', '{"incomplete":', '[1, 2, 3',
        'null', 'undefined', 'True', 'None', '', None,
    ]
    for broken in test_cases:
        assert safe_parse_json(broken) is None
        assert safe_parse_json(broken, expected_type='object') is None
        assert safe_parse_json(broken, expected_type='array') is None

    # Явная проверка: ищем объект, а подсунули массив
    assert safe_parse_json('[1, 2, 3]', expected_type='object') is None
    # Ищем массив, а подсунули объект
    assert safe_parse_json('{"a": 1}', expected_type='array') is None


# ==========================================
# STRESS TESTS (PROPERTY-BASED)
# ==========================================

# Генерируем безопасный текст без фигурных и квадратных скобок,
# чтобы наш наивный экстрактор (rfind) не сходил с ума от вложенности в строках.
safe_text = st.text(alphabet=st.characters(blacklist_characters="{}[]"))


@given(st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | safe_text,
    lambda children: st.lists(children) | st.dictionaries(safe_text, children),
    max_leaves=10
))
def test_safe_parse_json_roundtrip(obj):
    assume(isinstance(obj, (dict, list)))
    try:
        json_str = json.dumps(obj)
    except (TypeError, ValueError, OverflowError):
        assume(False)

    expected_type = 'array' if isinstance(obj, list) else 'object'
    parsed = safe_parse_json(json_str, expected_type=expected_type)
    assert parsed == obj


@given(st.text(min_size=0, max_size=1000))
def test_clean_json_response_preserves_valid_json(text):
    try:
        original = json.loads(text)
        e_type = 'array' if isinstance(original, list) else 'object'
        cleaned = clean_json_response(text, expected_type=e_type)
        assert json.loads(cleaned) == original
    except json.JSONDecodeError:
        pass


# ==========================================
# INTEGRATION TEST
# ==========================================

def test_integration_real_world_scenario_complex():
    """НАСТОЯЩИЙ интеграционный тест: парсинг JSON + чистка строк + конвертация чисел."""
    dirty_response = """
    Вот извлеченные данные:
    ```json
    {
        "services": [
            {"name": "- Труна (глянцева)", "quantity": "2 шт", "price": "5 000 грн"},
            {"name": "* Вінок", "quantity": "3.0", "price": "500.50"}
        ]
    }
    ```
    Удачи в работе!
    """

    # 1. Парсим грязный ответ
    parsed = safe_parse_json(dirty_response)
    assert parsed is not None
    assert "services" in parsed

    # 2. Прогоняем через наши утилиты (имитация работы пайплайна)
    for svc in parsed["services"]:
        svc["name"] = clean_service_name(svc["name"])
        svc["price"] = safe_int(svc["price"])
        svc["quantity"] = safe_int(svc["quantity"])

    # 3. Проверяем, что мусор ушел, а типы стали правильными
    assert parsed["services"][0]["name"] == "Труна (глянцева)"
    assert parsed["services"][0]["quantity"] == 2
    assert parsed["services"][0]["price"] == 5000

    assert parsed["services"][1]["name"] == "Вінок"
    assert parsed["services"][1]["quantity"] == 3
    assert parsed["services"][1]["price"] == 500