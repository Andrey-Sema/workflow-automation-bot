import json
import time
import logging
from typing import Dict, List, Any, Tuple
import google.generativeai as genai

from src.config import SERVICES_JSON, CEMETERIES_JSON, PRICES, PERSONNEL_TARIFFS, TEXT_MODEL_NAME
from src.utils import safe_parse_json

logger = logging.getLogger(__name__)
TEXT_MODEL = genai.GenerativeModel(TEXT_MODEL_NAME)

SRV_TRANSPORT = "Перевезення покійного"
SRV_EXTRA_SNOS = "снос (доп. точка)"
SRV_EXTRA_CERE = "церемоніймейстер (доп. точка)"


def _process_personnel_logic(services: List[Dict], num_addresses: int) -> Tuple[List[Dict], List[str]]:
    new_services, new_warnings = [], []
    total_personnel_qty = 0
    has_personnel = False

    for s in services:
        price = s.get("price", 0)
        if price in PERSONNEL_TARIFFS:
            name, qty = PERSONNEL_TARIFFS[price]
            s["name"] = name
            s["quantity"] = qty
            s["price"] = PRICES["snos_base"] if "ескорт" not in name else PRICES["snos_escort_base"]
            logger.debug(f"⚙️ Расшифрован персонал: {name} x{qty}")

        if s["name"] in ["снос", "снос-ескорт", "Завантаження/розвантаження"]:
            has_personnel = True
            total_personnel_qty += s.get("quantity", 0)

    if has_personnel and num_addresses >= 1:
        extra_qty = total_personnel_qty * num_addresses
        new_services.append({"name": SRV_EXTRA_SNOS, "price": PRICES["extra_point"], "quantity": extra_qty})
        new_warnings.append(f"⚠️ Добавлено {extra_qty} доп. точек для персонала.")

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
        new_services.append({"name": SRV_TRANSPORT, "price": PRICES["transport_base"], "quantity": num_addresses})
        new_warnings.append(f"⚠️ Добавлена упущенная перевозка ({num_addresses} адр.).")

    return new_services, new_warnings


def _process_ceremony_logic(services: List[Dict], num_addresses: int) -> Tuple[List[Dict], List[str]]:
    new_services, new_warnings = [], []
    ceremony_names = ["Церемоніймейстер", "Церемоніймейстер-РАНГ 2-й", "Церемоніймейстер-РАНГ 1-й"]

    if any(s["name"] in ceremony_names for s in services) and num_addresses >= 1:
        new_services.append({"name": SRV_EXTRA_CERE, "price": PRICES["extra_point"], "quantity": num_addresses})
        new_warnings.append("⚠️ Добавлена доп. точка для церемониймейстера.")

    return new_services, new_warnings


def apply_business_rules_in_python(data: Dict[str, Any], num_addresses: int) -> Dict[str, Any]:
    num_addresses = max(0, num_addresses)
    services = data.get("services", [])
    warnings = data.get("warnings", [])

    for s in services:
        s["price"] = max(0, int(s.get("price", 0)))
        s["quantity"] = max(1, int(s.get("quantity", 1)))

    extra_personnel, pers_warn = _process_personnel_logic(services, num_addresses)
    extra_transport, trans_warn = _process_transport_logic(services, num_addresses)
    extra_ceremony, cere_warn = _process_ceremony_logic(services, num_addresses)

    services.extend(extra_personnel + extra_transport + extra_ceremony)
    warnings.extend(pers_warn + trans_warn + cere_warn)

    data["services"] = services
    data["calculated_total"] = sum(s["price"] * s["quantity"] for s in services)
    data["warnings"] = warnings
    return data


def validate_and_normalize(raw_json_str: str, num_addresses: int, booked_in_1c: List[str], retries: int = 3) -> Dict[
    str, Any]:
    logger.info(f"🧠 АГЕНТ 2: Нормализация данных. Адресов: {num_addresses}")

    prompt = f"""
    Ты — строгий Data Engineer. Твоя задача: нормализовать JSON и убрать дубликаты.
    СЫРЫЕ ДАННЫЕ: {raw_json_str}
    СПРАВОЧНИК: {SERVICES_JSON}
    ЧЕРНЫЙ СПИСОК (Уже в 1С): {booked_in_1c}

    ЗАДАЧИ:
    1. ФИО -> Title Case. 
    2. Кладбища -> только из списка {CEMETERIES_JSON}.
    3. Если услуга в Черном списке — УДАЛИ ЕЁ.
    4. Автобусы (18/30 мест): определи название по цене.

    Верни ТОЛЬКО чистый JSON (объект).
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