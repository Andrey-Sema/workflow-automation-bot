import json
import time
import logging
from typing import Dict, List, Any, Tuple
from pathlib import Path
import google.generativeai as genai

# Импорты конфигурации (предполагаем, что SERVICES_JSON и CEMETERIES_JSON остались в config.py)
from src.config import SERVICES_JSON, CEMETERIES_JSON, TEXT_MODEL_NAME
from src.utils import safe_parse_json

logger = logging.getLogger(__name__)
TEXT_MODEL = genai.GenerativeModel(TEXT_MODEL_NAME)

# --- ЗАГРУЗКА БАЗЫ ЗНАНЬ (CATALOG.JSON) ---
BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"

try:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        CATALOG = json.load(f)
    logger.info("✅ Каталог 1С успешно загружен в память.")
except Exception as e:
    logger.critical(f"❌ ОШИБКА: Не удалось загрузить data/catalog.json! {e}")
    CATALOG = {}

# Вытягиваем нужные блоки из каталога
PRICES = CATALOG.get("tariffs", {})
DIGGING_RULES = CATALOG.get("digging_rules", {})
RAW_PERSONNEL = CATALOG.get("personnel_packages", {})
# Конвертируем ключи тарифов из строк в числа для удобного поиска
PERSONNEL_TARIFFS = {int(k): v for k, v in RAW_PERSONNEL.items()}
KNOWN_UNIT_PRICES = CATALOG.get("known_unit_prices", {})
CATALOG_MAPPING = CATALOG.get("catalog_1c_mapping", {})

# --- КОНСТАНТЫ УСЛУГ ---
SRV_TRANSPORT = "Перевезення покійного"
SRV_EXTRA_SNOS = "снос (доп. точка)"
SRV_EXTRA_CERE = "церемоніймейстер (доп. точка)"


def _apply_1c_mapping(item: Dict, category_key: str) -> bool:
    """Магия 1С: Подстановка точного имени и расчет 'Стрелочек вниз'."""
    mapping_list = CATALOG_MAPPING.get(category_key, [])
    target_price = item.get("price", 0)

    # Ищем индекс товара с такой же ценой
    match_index = -1
    for i, cat_item in enumerate(mapping_list):
        if cat_item["price"] == target_price:
            match_index = i
            break

    if match_index != -1:
        matched_item = mapping_list[match_index]
        exact_name = matched_item["name"]

        # Считаем, сколько раз это же имя встречалось ДО нашего индекса (чтобы нажать вниз)
        down_presses = sum(1 for i in range(match_index) if mapping_list[i]["name"] == exact_name)

        item["name"] = exact_name
        item["1c_down_presses"] = down_presses
        return True
    return False


def _process_complex_goods_and_mapping(goods: List[Dict], warnings: List[str]):
    """Обрабатывает сложные товары (гробы, кресты) и простые (свечи)."""
    for item in goods:
        name_lower = item.get("name", "").lower()
        price = item.get("price", 0)
        qty = item.get("quantity", 1)
        item["1c_down_presses"] = 0  # По умолчанию ноль нажатий

        # 1. Сначала пытаемся найти сложный товар в 1С (Маппинг)
        mapped = False
        if "труна" in name_lower or "гроб" in name_lower:
            mapped = _apply_1c_mapping(item, "coffins")
        elif "вінок" in name_lower or "венок" in name_lower:
            mapped = _apply_1c_mapping(item, "wreaths")
        elif "хрест" in name_lower or "крест" in name_lower:
            mapped = _apply_1c_mapping(item, "crosses")
        elif "корзина" in name_lower:
            mapped = _apply_1c_mapping(item, "baskets")
        elif "табличка" in name_lower:
            mapped = _apply_1c_mapping(item, "plaques")
        elif "рушник" in name_lower or "отче" in name_lower:
            mapped = _apply_1c_mapping(item, "towels")

        if mapped:
            continue  # Если нашли точное совпадение в каталоге, идем к следующему товару

        # 2. Если это не сложный товар, проверяем мелкоту (Платочки, Свечи)
        for known_name, valid_prices in KNOWN_UNIT_PRICES.items():
            if known_name.lower() in name_lower:
                # Если цена уже правильная - забиваем
                if price in valid_prices:
                    item["name"] = known_name  # Нормализуем имя
                    break
                # Если ИИ умножил цену на кол-во - исправляем
                elif qty > 1 and price % qty == 0 and (price // qty) in valid_prices:
                    item["price"] = price // qty
                    item["name"] = known_name
                    warnings.append(
                        f"⚠️ Математика каталога: Цена за '{known_name}' поделена на {qty}: стало {item['price']} за шт.")
                    break


def _process_personnel_logic(services: List[Dict], num_addresses: int) -> Tuple[List[Dict], List[str]]:
    new_services, new_warnings = [], []
    total_personnel_qty = 0
    has_personnel = False
    personnel_keywords = ["снос", "ескорт", "персонал", "завантаження", "розвантаження"]

    for s in services:
        original_name = s.get("name", "")
        name_lower = original_name.lower()
        price = s.get("price", 0)

        is_personnel_service = any(kw in name_lower for kw in personnel_keywords)

        if price in PERSONNEL_TARIFFS and is_personnel_service:
            pkg = PERSONNEL_TARIFFS[price]
            s["name"] = pkg["name"]
            s["quantity"] = pkg["qty"]
            s["price"] = PRICES.get("snos_base", 1300) if "ескорт" not in pkg["name"] else PRICES.get(
                "snos_escort_base", 2000)
            logger.debug(f"⚙️ Расшифрован персонал: {s['name']} x{s['quantity']} (было: '{original_name}')")

        current_name = s.get("name", "")
        if current_name in ["снос", "снос-ескорт", "Завантаження/розвантаження"] or is_personnel_service:
            has_personnel = True
            total_personnel_qty += s.get("quantity", 0)

    if has_personnel and num_addresses >= 1:
        extra_qty = total_personnel_qty * num_addresses
        new_services.append({"name": SRV_EXTRA_SNOS, "price": PRICES.get("extra_point", 400), "quantity": extra_qty})
        new_warnings.append(f"⚠️ Добавлено {extra_qty} доп. точек для персонала.")

    return new_services, new_warnings


def _process_digging_logic(services: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """Логика для 'Закопування/опускання труни' на базе catalog.json."""
    new_services, new_warnings = [], []
    base_price = DIGGING_RULES.get("base_price_per_person", 1600)
    towel_prices = DIGGING_RULES.get("towel_prices", [1400, 2000])

    for s in services:
        if "закопування" in s.get("name", "").lower() or "опускання" in s.get("name", "").lower():
            raw_price = s.get("price", 0)
            towel_found_price = 0
            base_digging_total = raw_price

            # Ищем, зашит ли в цену рушник
            for tp in towel_prices:
                if (raw_price - tp) % base_price == 0 and (raw_price - tp) > 0:
                    towel_found_price = tp
                    base_digging_total = raw_price - tp
                    break

            # Если мы определили базовую сумму закопки
            if base_digging_total % base_price == 0 and base_digging_total > 0:
                correct_qty = base_digging_total // base_price
                s["price"] = base_price
                s["quantity"] = correct_qty

                # Добавляем рушник отдельной строкой
                if towel_found_price > 0:
                    new_services.append({
                        "name": "Рушник для опускання",
                        "price": towel_found_price,
                        "quantity": 1
                    })
                    new_warnings.append(f"⚠️ Из Закопки ({raw_price}) выделен Рушник ({towel_found_price}).")

                logger.debug(f"⚙️ Расшифрована Закопка: {correct_qty} чел по {base_price}.")

    return new_services, new_warnings


def _process_transport_logic(services: List[Dict], num_addresses: int) -> Tuple[List[Dict], List[str]]:
    new_services, new_warnings = [], []
    transport_found = False

    for s in services:
        if s["name"] == SRV_TRANSPORT:
            transport_found = True
            if s["quantity"] != num_addresses and num_addresses > 0:
                old_qty = s["quantity"]
                s["quantity"] = num_addresses
                new_warnings.append(f"⚠️ Перевозки скорректированы: {old_qty} -> {num_addresses}")

    if not transport_found and num_addresses > 0:
        new_services.append(
            {"name": SRV_TRANSPORT, "price": PRICES.get("transport_base", 1000), "quantity": num_addresses})
        new_warnings.append(f"⚠️ Добавлена упущенная перевозка ({num_addresses} адр.).")

    return new_services, new_warnings


def _process_ceremony_logic(services: List[Dict], num_addresses: int) -> Tuple[List[Dict], List[str]]:
    new_services, new_warnings = [], []
    ceremony_names = ["Церемоніймейстер", "Церемоніймейстер-РАНГ 2-й", "Церемоніймейстер-РАНГ 1-й"]

    if any(s["name"] in ceremony_names for s in services) and num_addresses >= 1:
        new_services.append(
            {"name": SRV_EXTRA_CERE, "price": PRICES.get("extra_point", 400), "quantity": num_addresses})
        new_warnings.append("⚠️ Добавлена доп. точка для церемониймейстера.")

    return new_services, new_warnings


def apply_business_rules_in_python(data: Dict[str, Any], num_addresses: int) -> Dict[str, Any]:
    num_addresses = max(0, num_addresses)

    services = data.get("services", [])
    goods = data.get("goods", [])
    transport = data.get("transport", [])
    warnings = data.get("warnings", [])

    # Нормализуем базовые вещи
    for cat in [services, goods, transport]:
        for item in cat:
            item["price"] = max(0, int(item.get("price", 0)))
            item["quantity"] = max(1, int(item.get("quantity", 1)))
            item["1c_down_presses"] = 0  # Инициализация для сервисов и транспорта тоже

    # Логика услуг
    extra_personnel, pers_warn = _process_personnel_logic(services, num_addresses)
    extra_transport, trans_warn = _process_transport_logic(services, num_addresses)
    extra_ceremony, cere_warn = _process_ceremony_logic(services, num_addresses)
    extra_digging, dig_warn = _process_digging_logic(services)

    services.extend(extra_personnel + extra_transport + extra_ceremony + extra_digging)
    warnings.extend(pers_warn + trans_warn + cere_warn + dig_warn)

    # Логика товаров (МАГИЯ 1С)
    _process_complex_goods_and_mapping(goods, warnings)

    # Также прогоним маппинг по транспорту на всякий случай, если ты захочешь добавить его в каталог
    _process_complex_goods_and_mapping(transport, warnings)

    data["services"] = services
    data["goods"] = goods
    data["transport"] = transport
    data["warnings"] = warnings

    return data


def validate_and_normalize(raw_json_str: str, num_addresses: int, booked_in_1c: List[str], retries: int = 3) -> Dict[
    str, Any]:
    logger.info(f"🧠 АГЕНТ 2: Нормализация данных. Адресов: {num_addresses}")

    prompt = f"""
    Ты — строгий Data Engineer. Твоя задача: нормализовать названия в JSON.
    СЫРЫЕ ДАННЫЕ: {raw_json_str}
    СПРАВОЧНИК УСЛУГ: {SERVICES_JSON}
    ЧЕРНЫЙ СПИСОК (Уже в 1С): {booked_in_1c}

    ЗАДАЧИ:
    1. ФИО -> Title Case. 
    2. Кладбища -> только из списка {CEMETERIES_JSON}.
    3. Если услуга в Черном списке — УДАЛИ ЕЁ.
    4. КЛЮЧЕВОЕ: Сохрани массивы "goods", "transport" и поле "handwritten_total" БЕЗ ИЗМЕНЕНИЙ! Нормализуй только "services".

    Верни ТОЛЬКО чистый JSON-объект.
    """

    for attempt in range(retries):
        try:
            response = TEXT_MODEL.generate_content(prompt, generation_config={"temperature": 0.0})
            data = safe_parse_json(response.text, expected_type='object')

            if data:
                return apply_business_rules_in_python(data, num_addresses)

        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt + 1} не удалась: {e}")
            time.sleep(2)

    return {}