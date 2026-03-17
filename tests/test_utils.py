import pytest
import math
from hypothesis import given, strategies as st, assume
from src.utils import safe_int, clean_service_name, clean_json_response, safe_parse_json

# ==========================================
# КОНСТАНТЫ ДЛЯ ТЕСТОВ
# ==========================================
MAX_PG_INT = 2147483647
MIN_PG_INT = -2147483648


# ==========================================
# ТЕСТЫ ДЛЯ safe_int
# ==========================================

@given(st.integers(min_value=MIN_PG_INT, max_value=MAX_PG_INT))
def test_safe_int_returns_same_integer(val):
    """Числа в пределах БД не должны меняться."""
    assert safe_int(val) == val


@given(st.integers().filter(lambda x: x > MAX_PG_INT or x < MIN_PG_INT))
def test_safe_int_handles_db_overflow(val):
    """Числа вне лимитов БД должны возвращать default (защита от переполнения)."""
    assert safe_int(val, default=0) == 0
    # Проверяем с другим дефолтом
    assert safe_int(val, default=999) == 999


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_safe_int_handles_floats(val):
    """
    Дроби должны конвертироваться, но с защитой от переполнения БД.
    Если float в пределах БД - конвертируем, если нет - default.
    """
    result = safe_int(val)
    assert isinstance(result, int)

    # Учитываем защиту БД
    if MIN_PG_INT <= val <= MAX_PG_INT:
        assert result == int(val)
    else:
        assert result == 0  # default


def test_safe_int_infinity_and_nan():
    """Явная проверка обработки бесконечностей и NaN."""
    test_cases = [
        (float('inf'), 0),
        (float('-inf'), 0),
        (float('nan'), 0),
        ("Infinity", 0),
        ("-Infinity", 0),
        ("NaN", 0),
    ]

    for value, expected in test_cases:
        assert safe_int(value, default=0) == expected
        # Проверяем с кастомным дефолтом
        assert safe_int(value, default=42) == 42


def test_safe_int_preserves_sign():
    """Проверка, что отрицательные числа сохраняют знак (если в пределах БД)."""
    test_cases = [
        ("-100", -100),
        ("-100.5", -100),
        ("  -100  грн ", -100),
        ("-2147483648", -2147483648),  # MIN_PG_INT
        ("-2147483649", 0),  # Вне лимита -> default
    ]

    for value, expected in test_cases:
        assert safe_int(value, default=0) == expected


def test_safe_int_edge_cases():
    """Проверка граничных значений с защитой БД."""
    assert safe_int(str(MAX_PG_INT)) == MAX_PG_INT
    assert safe_int(str(MIN_PG_INT)) == MIN_PG_INT

    # Числа вне диапазона
    assert safe_int(str(MAX_PG_INT + 1), default=0) == 0
    assert safe_int(str(MIN_PG_INT - 1), default=0) == 0

    # Граничные значения как float
    assert safe_int(float(MAX_PG_INT)) == MAX_PG_INT
    assert safe_int(float(MAX_PG_INT) + 0.5, default=0) == 0


@given(st.text())
def test_safe_int_never_crashes_on_garbage_text(text):
    """Самый жесткий тест. Любой текст -> int без ошибок."""
    result = safe_int(text, default=0)
    assert isinstance(result, int)


def test_safe_int_known_cases():
    """Точечные проверки реальных кейсов."""
    test_cases = [
        ("  1 500 грн ", 1500),
        ("10.5 $", 10),
        (None, 99, 99),  # с кастомным default
        ("", 0),
        ("   ", 0),
        ("0", 0),
        ("-0", 0),
        ("3,200.50", 3200),
        ("ціна 500", 500),
        ("500.00", 500),
        ("500.99", 500),  # округление вниз (int())
    ]

    for case in test_cases:
        if len(case) == 3:
            value, expected, default = case
            assert safe_int(value, default) == expected
        else:
            value, expected = case
            assert safe_int(value) == expected


# ==========================================
# ТЕСТЫ ДЛЯ clean_service_name
# ==========================================

@given(st.text())
def test_clean_service_name_never_crashes(text):
    """Любая дичь на входе -> строка."""
    result = clean_service_name(text)
    assert isinstance(result, str)


@given(st.text())
def test_clean_service_name_strips_spaces_and_hyphens(text):
    """После очистки по краям не должно быть мусора."""
    result = clean_service_name(text)
    if result:
        assert result == result.strip()
        # В начале не должно быть маркеров списка
        assert not result.startswith(('-', '*', '•', '·'))
        # В конце не должно быть висячих дефисов
        assert not result.endswith(('-', '—', '–'))


def test_clean_service_name_known_cases():
    """Проверка реальных кейсов с учетом новых правил."""
    test_cases = [
        ("  Труна  ", "Труна"),
        ("- Вінок", "Вінок"),
        ("* Хусточки", "Хусточки"),
        ("«Свічки»", "Свічки"),
        ("  -   Катафалк   ", "Катафалк"),
        ("1. Труна (глянцева)", "Труна (глянцева)"),
        ("• Пронос труни", "Пронос труни"),
        ("· Подушка", "Подушка"),
        (None, ""),  # <-- ВАЖНО: теперь пустая строка, а не "None"
        ("", ""),
        ("   ", ""),
        ("-", ""),
        ("*", ""),
        ("123", "123"),  # цифры сохраняются
        ("-123", "123"),  # дефис в начале удаляется
    ]

    for input_val, expected in test_cases:
        assert clean_service_name(input_val) == expected


# ==========================================
# ТЕСТЫ ДЛЯ clean_json_response
# ==========================================

def test_clean_json_response_removes_markdown():
    """Проверка удаления markdown-блоков."""
    test_cases = [
        ("```json\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
        ("```\n{\"key\": \"value\"}\n```", "{\"key\": \"value\"}"),
        ("Текст до\n{\"key\": \"value\"}\nТекст после", "{\"key\": \"value\"}"),
        ("```json\n[1, 2, 3]\n```", "[1, 2, 3]"),
        ("```\n[1, 2, 3]\n```", "[1, 2, 3]"),
        ("Текст\n[1, 2, 3]\nТекст", "[1, 2, 3]"),
        ("{без оберток}", "{без оберток}"),
    ]

    for dirty, expected in test_cases:
        assert clean_json_response(dirty) == expected


def test_clean_json_response_with_expected_type():
    """Проверка параметра expected_type."""
    response = "Текст\n[1, 2, 3]\nТекст"

    # Для array ищем скобки []
    assert clean_json_response(response, expected_type='array') == "[1, 2, 3]"

    # Для object ищем {} (и не находим)
    assert clean_json_response(response, expected_type='object') == response.strip()

    # Смешанный контент
    mixed = "Текст\n{\"a\": 1}\nТекст\n[1, 2, 3]\nТекст"
    assert clean_json_response(mixed, expected_type='object') == "{\"a\": 1}"
    assert clean_json_response(mixed, expected_type='array') == "[1, 2, 3]"


def test_clean_json_response_empty_input():
    """Проверка на пустые значения."""
    assert clean_json_response("") == ""
    assert clean_json_response(None) == ""
    assert clean_json_response("   ") == "   "  # пробелы сохраняются


# ==========================================
# ТЕСТЫ ДЛЯ safe_parse_json
# ==========================================

def test_safe_parse_json_valid():
    """Проверка парсинга валидного JSON."""
    assert safe_parse_json('{"key": "value"}') == {"key": "value"}
    assert safe_parse_json('[1, 2, 3]', expected_type='array') == [1, 2, 3]
    assert safe_parse_json('{"nested": {"a": 1}}') == {"nested": {"a": 1}}


def test_safe_parse_json_with_markdown():
    """Проверка парсинга JSON с markdown-оберткой."""
    test_cases = [
        ("```json\n{\"key\": \"value\"}\n```", {"key": "value"}),
        ("```\n{\"key\": \"value\"}\n```", {"key": "value"}),
        ("Текст до\n{\"key\": \"value\"}\nТекст после", {"key": "value"}),
        ("```json\n[1, 2, 3]\n```", [1, 2, 3]),
    ]

    for dirty, expected in test_cases:
        result = safe_parse_json(dirty)
        assert result == expected


def test_safe_parse_json_broken():
    """Обработка битого JSON."""
    test_cases = [
        '<html>502 Bad Gateway</html>',
        '{"incomplete":',
        '[1, 2, 3',
        'null',
        'undefined',
        'True',  # Python bool, не JSON
        'None',
        '',
        None,
    ]

    for broken in test_cases:
        assert safe_parse_json(broken) is None
        # Проверяем с явным указанием типа
        assert safe_parse_json(broken, expected_type='object') is None
        assert safe_parse_json(broken, expected_type='array') is None


# ==========================================
# СТРЕСС-ТЕСТЫ (PROPERTY-BASED)
# ==========================================

@given(st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=10
))
def test_safe_parse_json_roundtrip(obj):
    """Проверка: JSON -> строка -> парсинг -> объект должен совпадать."""
    import json
    try:
        json_str = json.dumps(obj)
    except:
        # Некоторые объекты (например, set) не сериализуются
        return

    parsed = safe_parse_json(json_str)
    assert parsed == obj


@given(st.text(min_size=0, max_size=1000))
def test_clean_json_response_preserves_valid_json(text):
    """Если строка уже является валидным JSON, очистка не должна его ломать."""
    import json
    try:
        # Пробуем распарсить как JSON
        original = json.loads(text)
        cleaned = clean_json_response(text)
        # Очищенная строка должна парситься в тот же объект
        assert json.loads(cleaned) == original
    except json.JSONDecodeError:
        # Если это невалидный JSON, тест не применим
        pass


# ==========================================
# ИНТЕГРАЦИОННЫЙ ТЕСТ (УСЛОЖНЕННАЯ ВЕРСИЯ)
# ==========================================

def test_integration_real_world_scenario_complex():
    """Тест на сложном реальном сценарии с разными вариантами мусора."""

    # Эмулируем ответ от Gemini с разными видами мусора
    dirty_responses = [
        # Вариант 1: с markdown и лишним текстом
        """
        Вот извлеченные данные:
        ```json
        {
            "services": [
                {"name": "- Труна", "quantity": "2 шт", "price": "5 000 грн"},
                {"name": "* Вінок", "quantity": "3", "price": "500.50"},
                {"name": "  Хусточки  ", "quantity": "inf", "price": null},
                {"name": "• Свічки", "quantity": "10", "price": "45.00"}
            ]
        }"""
]