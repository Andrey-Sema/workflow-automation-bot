import json
import time
import logging
import re
import google.generativeai as genai
from src.config import SERVICES_JSON, CEMETERIES_JSON

logger = logging.getLogger(__name__)

# 🔥 Оставляем быструю и стабильную Lite версию
TEXT_MODEL = genai.GenerativeModel('gemini-3.1-flash-lite-preview')


def clean_json_response(text: str) -> str:
    """Умный поиск JSON (по совету GPT)"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group()
    return text.strip()


def apply_business_rules_in_python(data: dict, num_addresses: int) -> dict:
    """
    ЭТО НАШ PYTHON RULES ENGINE.
    Здесь 100% детерминированная логика, которая никогда не галлюцинирует.
    """
    services = data.get("services", [])
    warnings = data.get("warnings", [])

    # 1. Жесткая типизация (чтобы не было строк вместо чисел)
    for s in services:
        s["price"] = int(s.get("price", 0))
        s["quantity"] = int(s.get("quantity", 1))

    new_services = []
    has_personnel = False
    personnel_count = 0
    has_transportation = False

    # 2. Анализируем то, что выдала LLM
    for s in services:
        name = s["name"]

        # Ищем, есть ли перевозка
        if name == "Перевезення покійного":
            has_transportation = True
            if num_addresses >= 1:
                s["price"] = 1000
                s["quantity"] = num_addresses

        # Ищем персонал, чтобы потом накинуть им доп. точки
        if name in ["снос", "снос-ескорт", "Завантаження/розвантаження"]:
            has_personnel = True
            personnel_count += s["quantity"]

        # Ищем церемониймейстера для доп. точки
        if name in ["Церемоніймейстер", "Церемоніймейстер-РАНГ 2-й", "Церемоніймейстер-РАНГ 1-й"]:
            if num_addresses >= 1:
                new_services.append({
                    "name": "церемоніймейстер (доп. точка)",
                    "price": 400,
                    "quantity": num_addresses
                })
                warnings.append(f"⚠️ Бот добавил доп. точку для Церемониймейстера ({num_addresses} шт).")

    # 3. Применяем правила по адресам
    if num_addresses >= 1:
        # Добавляем перевозку, если агент ее проебал
        if not has_transportation:
            services.append({
                "name": "Перевезення покійного",
                "price": 1000,
                "quantity": num_addresses
            })
            warnings.append(f"⚠️ Агент проебался: не указана 'Перевезення покійного'. Бот добавил {num_addresses} шт.")

        # Добавляем доп. точки персоналу
        if has_personnel:
            extra_points_qty = personnel_count * num_addresses
            already_has_extra = any(x["name"] == "снос (доп. точка)" for x in services)
            if not already_has_extra:
                new_services.append({
                    "name": "снос (доп. точка)",
                    "price": 400,
                    "quantity": extra_points_qty
                })
                warnings.append(f"⚠️ Агент проебался: не посчитаны доп. точки персоналу. Бот добавил {extra_points_qty} шт (Люди * Адреса).")

    # Сливаем базовые услуги и те, что сгенерировал Питон
    data["services"] = services + new_services

    # 4. Считаем ИТОГО в Питоне
    data["calculated_total"] = sum(s["price"] * s["quantity"] for s in data["services"])
    data["warnings"] = warnings

    return data


def validate_and_normalize(raw_json_str: str, num_addresses: int, booked_in_1c: list, retries: int = 3) -> dict:
    logger.info("🧠 АГЕНТ 2 (Text Flash Lite): Извлекаю факты, нормализую и чищу дубликаты...")

    prompt = f"""
    Ты — строгий Data Engineer. Твоя задача ТОЛЬКО нормализовать данные, найти синонимы и убрать дубликаты. Никакой математики!
    Вот сырой JSON из бланка заказа:
    {raw_json_str}

    ДОВІДНИК ПОСЛУГ (JSON): {SERVICES_JSON}
    ДОВІДНИК КЛАДОВИЩ (JSON): {CEMETERIES_JSON}
    УЖЕ ЗАБРОНИРОВАНО В 1С (Черный список): {booked_in_1c}

    ПРАВИЛА ИЗВЛЕЧЕНИЯ ФАКТОВ:
    1. ДАТА РОЖДЕНИЯ: Если пусто или нет, ставь "01.01.1920".
    2. НОРМАЛИЗАЦИЯ: ФИО в Title Case. Кладбища на русский. "Оформлення необхідних документів" -> "доки".

    3. АВТОБУСЫ:
       Если в сыром JSON есть "Автотранспорт" и места (18 или 30), определи название по ЦЕНЕ:
       - 18 мест, цена < 5200 -> "Доп 18 місць (Автотранспорт под людей)"
       - 18 мест, цена >= 5200 -> "Доп 18 місць (Підвищеної комфортності) (Автотранспорт под людей)"
       - 30 мест, цена < 6500 -> "Доп 30 місць (Автотранспорт под людей)"
       - 30 мест, цена >= 6500 -> "Доп 30 місць (Підвищеної комфортності) (Автотранспорт под людей)"

    4. РАСШИФРОВКА ПЕРСОНАЛА (ФУЗЗИ ЛОГИКА - ВАЖНО!):
       ОБЯЗАТЕЛЬНО проверяй цену услуги сноса (Послуги персоналу/Плащи/Ескорт), ДАЖЕ ЕСЛИ quantity УЖЕ НЕ 1.
       - Если цена 5200 -> "снос" (price: 1300, quantity: 4)
       - Если цена 7800 -> "снос" (price: 1300, quantity: 6)
       - Если цена 10400 -> "снос" (price: 1300, quantity: 8)
       - Если цена 8000 -> "снос-ескорт" (price: 2000, quantity: 4)
       - Если цена 12000 -> "снос-ескорт" (price: 2000, quantity: 6)
       - Если цена 16000 -> "снос-ескорт" (price: 2000, quantity: 8)
       НЕ ДОБАВЛЯЙ НИКАКИЕ ДОП ТОЧКИ И ПЕРЕВОЗКИ САМ!

    5. КОМБИНИРОВАННЫЕ ПОЗИЦИИ:
       Если в сыром JSON позиция склеена через слэш (например, "Насипний пагорб/Встанов. хреста" за 1000) и ты разбиваешь её на две услуги из справочника, ОБЯЗАТЕЛЬНО распредели цену между ними логично, или оставь как ОДНУ позицию, если не уверен. НЕ дублируй исходную цену на обе позиции!

    6. ДЕДУПЛИКАЦИЯ (ВАЖНО!): 
       Если услуга из черного списка УЖЕ забита в 1С (или её синоним) — ИСКЛЮЧИ её из ответа!

    7. МУСОР: Если слова нет в справочнике, пиши "ОШИБКА ВАЛИДАЦИИ: [оригинал]".

    Структура ответа:
    {{
      "deceased": {{"fio": "ФИО", "birth_date": "Дата", "death_date": "", "burial_date": "", "cemetery": "Кладбище"}},
      "customer": {{"fio": ""}},
      "services": [{{"name": "ИМЯ ИЗ СПРАВОЧНИКА", "price": 0, "quantity": 1}}]
    }}
    Верни ТОЛЬКО JSON.
    """

    for attempt in range(retries):
        try:
            response = TEXT_MODEL.generate_content(
                prompt,
                generation_config={"temperature": 0, "top_p": 0.1},
                request_options={"timeout": 60}
            )

            logger.info(f"✅ Получен ответ от Агента 2 (длина: {len(response.text)})")

            clean_str = clean_json_response(response.text)
            if not clean_str: raise ValueError("Пустой ответ")
            data = json.loads(clean_str)

            data.setdefault("services", [])
            data.setdefault("warnings", [])

            # 🔥 ПРОГОНЯЕМ ЧЕРЕЗ ПИТОН-ДВИЖОК 🔥
            final_data = apply_business_rules_in_python(data, num_addresses)

            return final_data

        except Exception as e:
            logger.warning(f"⚠️ АГЕНТ 2 споткнулся ({attempt + 1}/{retries}) [{type(e).__name__}]: {e}")
            time.sleep(2 ** attempt)

    logger.error("❌ АГЕНТ 2 сдался.")
    return {}