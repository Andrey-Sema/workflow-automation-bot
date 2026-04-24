import re
import json
import logging
import math
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_PG_INT = 2147483647
MIN_PG_INT = -2147483648
LIST_MARKERS = r'^[\s\d]*[-\*•·\.]\s*'

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

def fix_temporal_hallucinations(date_str: str) -> str:
    """
    Щит від помилок OCR: чистить крапки і фіксить рік.
    """
    if not date_str:
        return ""

    try:
        date_str = date_str.strip().rstrip('.')
        now = datetime.now()

        # Строгая проверка: ровно две цифры, точка, две цифры (например, 26.03)
        if re.match(r'^\d{2}\.\d{2}$', date_str):
            return f"{date_str}.{now.year}"

        # 2. Якщо прислали тільки день і місяць (без року), відразу додаємо поточний
        if len(date_str) <= 5 and '.' in date_str:
            return f"{date_str}.{now.year}"

        # 3. Перевіряємо рік на адекватність
        dt = datetime.strptime(date_str, "%d.%m.%Y")

        if dt.year == now.year:
            return date_str  # Все чітко

        # 4. Новорічний виняток: зараз січень, а смерть була в грудні минулого року
        if now.month == 1 and dt.month == 12 and dt.year == (now.year - 1):
            return date_str

        # 5. Всі інші розбіжності року — це галюцинація, форсуємо поточний рік
        fixed_dt = dt.replace(year=now.year)
        return fixed_dt.strftime("%d.%m.%Y")

    except ValueError:
        # Якщо дата в зовсім незрозумілому форматі, просто повертаємо почищену від крапки
        return date_str
def parse_number_string(value: str) -> Optional[float]:
    if not isinstance(value, str):
        return None

    # Исправлено: агрессивная чистка. Убираем вообще ВСЁ, кроме цифр, минуса, точки и запятой.
    # Это решает проблему со словами типа "ціна 500"
    cleaned = re.sub(r'[^\d.,\-]', '', value)

    if not cleaned or cleaned == '-':
        return None

    if ',' in cleaned and '.' in cleaned:
        if cleaned.index(',') < cleaned.index('.'):
            cleaned = cleaned.replace(',', '')
        else:
            cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        parts = cleaned.split(',')
        if len(parts) == 2 and len(parts[1]) in (1, 2) and parts[1].isdigit():
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')

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