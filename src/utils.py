import re
import json
import logging
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
from datetime import datetime
import math

logger = logging.getLogger(__name__)

# Константы для защиты БД
MAX_PG_INT = 2147483647
MIN_PG_INT = -2147483648

# Символы-маркеры списка для удаления
LIST_MARKERS = r'^[\s\d]*[-\*•·]\s*'


# ==================== ОЧИСТКА JSON ====================

def clean_json_response(text: Any, expected_type: str = 'object') -> str:
    """
    Универсальная очистка JSON от markdown и мусора.

    Args:
        text: Сырой текст от нейронки (может быть None)
        expected_type: 'object' для {...} или 'array' для [...]

    Returns:
        Очищенную JSON-строку или пустую строку
    """
    if text is None:
        return ""

    # Преобразуем в строку, если пришел не текст
    text = str(text).strip()

    # Убираем markdown-обертки
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    # Ищем нужную структуру
    if expected_type == 'object':
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]
    else:  # array
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end != -1 and start < end:
            return text[start:end + 1]

    # Если ничего не нашли, возвращаем оригинал (но без лишних пробелов)
    return text.strip()


def safe_parse_json(text: Any, expected_type: str = 'object') -> Optional[Union[Dict, List]]:
    """
    Безопасно парсит JSON с предварительной очисткой.

    Returns:
        Распарсенный объект или None при ошибке
    """
    try:
        cleaned = clean_json_response(text, expected_type)
        if not cleaned:
            return None
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.debug(f"JSON parse error: {e}")
        return None


# ==================== РАБОТА С ЧИСЛАМИ ====================

def parse_number_string(value: str) -> Optional[float]:
    """
    Парсит строку с числом, учитывая разные форматы.

    Примеры:
        "3,200.50" → 3200.5
        "1 500 грн" → 1500.0
        "10.5 $" → 10.5
    """
    if not isinstance(value, str):
        return None

    # Убираем пробелы и символы валют
    cleaned = re.sub(r'[₴₽$€]|\b(грн|руб|usd|eur)\b', '', value, flags=re.I)
    cleaned = cleaned.strip()

    # Если строка пустая, выходим
    if not cleaned:
        return None

    # Заменяем пробелы между цифрами
    cleaned = re.sub(r'(?<=\d)\s+(?=\d)', '', cleaned)

    # Определяем формат числа
    if ',' in cleaned and '.' in cleaned:
        # Формат типа "3,200.50" - запятая как разделитель тысяч
        if cleaned.index(',') < cleaned.index('.'):
            # Убираем запятые (разделители тысяч)
            cleaned = cleaned.replace(',', '')
        else:
            # Европейский формат: "3.200,50" - точка как разделитель тысяч
            cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        # Только запятая - может быть разделителем тысяч или десятичным
        # Проверяем, есть ли после запятой ровно 2 цифры (обычно копейки)
        parts = cleaned.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2) and parts[1].isdigit():
            # Скорее всего десятичная запятая
            cleaned = cleaned.replace(',', '.')
        else:
            # Скорее всего разделитель тысяч
            cleaned = cleaned.replace(',', '')

    # Убираем все оставшиеся пробелы
    cleaned = re.sub(r'\s+', '', cleaned)

    try:
        return float(cleaned)
    except ValueError:
        return None


def safe_int(value: Any, default: int = 0) -> int:
    """
    Безопасное преобразование в int с защитой от переполнения БД.

    Args:
        value: Любое значение (строка, число, None)
        default: Значение по умолчанию при ошибке или переполнении

    Returns:
        int в пределах [MIN_PG_INT, MAX_PG_INT] или default
    """
    if value is None:
        return default

    # Если уже число
    if isinstance(value, (int, float)):
        # Проверяем на бесконечность и NaN
        if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
            return default

        # Проверяем границы БД
        if MIN_PG_INT <= value <= MAX_PG_INT:
            return int(value)
        return default

    # Если строка
    if isinstance(value, str):
        # Пробуем распарсить число
        num = parse_number_string(value)
        if num is not None:
            # Проверяем границы
            if MIN_PG_INT <= num <= MAX_PG_INT:
                return int(num)
        return default

    # Всё остальное
    return default


# ==================== РАБОТА С ТЕКСТОМ ====================

def clean_service_name(name: Any) -> str:
    """
    Очищает название услуги от мусора и маркеров списка.

    Удаляет:
        - Маркеры списка в начале (-, *, •, ·, 1., 2. и т.д.)
        - Лишние кавычки и пробелы
        - Висячие дефисы в конце
    """
    if name is None:
        return ""

    if not isinstance(name, str):
        name = str(name)

    # Сохраняем оригинал для проверки
    original = name

    # Удаляем маркеры списка в начале (включая точки после цифр)
    name = re.sub(LIST_MARKERS, '', name)

    # Если после удаления маркера строка не изменилась, пробуем удалить конкретные символы
    if name == original and original and original[0] in ('-', '*', '•', '·'):
        name = original[1:].lstrip()

    # Убираем кавычки разного типа
    name = name.strip('"\'«»„“')

    # Нормализуем пробелы
    name = re.sub(r'\s+', ' ', name)

    # Убираем висячие дефисы в конце
    name = re.sub(r'[-–—]\s*$', '', name)

    return name.strip()


def deduplicate_services(services: List[Dict]) -> List[Dict]:
    """Удаляет дубликаты услуг, суммируя количество и забирая макс. цену."""
    if not services:
        return []

    unique = {}
    for s in services:
        if not isinstance(s, dict):
            continue

        name = clean_service_name(s.get('name', ''))
        if not name or len(name) < 2:
            continue

        key = name.lower()
        if key in unique:
            # Суммируем количество
            unique[key]['quantity'] += safe_int(s.get('quantity', 1), default=1)
            # Берем максимальную цену
            unique[key]['price'] = max(
                unique[key].get('price', 0),
                safe_int(s.get('price', 0))
            )
            # Сумму пересчитаем позже
            if 'sum' in unique[key]:
                del unique[key]['sum']
        else:
            unique[key] = {
                'name': name,
                'quantity': safe_int(s.get('quantity', 1), default=1),
                'price': safe_int(s.get('price', 0))
            }
            if 'sum' in s:
                unique[key]['sum'] = safe_int(s.get('sum', 0))

    # Пересчитываем сумму для каждой услуги
    result = list(unique.values())
    for item in result:
        if 'sum' not in item:
            item['sum'] = item['quantity'] * item['price']

    return result