import os
import json
import time
import logging
from typing import Dict, List, Any
from pathlib import Path
from functools import lru_cache
from difflib import get_close_matches
from dotenv import load_dotenv # Добавили

from google import genai
from google.genai import types

from src.utils import safe_parse_json

# Загружаем переменные, чтобы клиент их увидел
load_dotenv()

logger = logging.getLogger(__name__)

# Явно передаем ключ из переменной GEMINI_API_KEY
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ... остальной код без изменений

BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"

try:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        CATALOG = json.load(f)
    logger.info("✅ 1C Catalog successfully loaded into memory.")
except Exception as e:
    logger.critical(f"❌ FATAL: Failed to load data/catalog.json! {e}")
    CATALOG = {}

DIGGING_RULES = CATALOG.get("digging_rules", {})
KNOWN_UNIT_PRICES = CATALOG.get("known_unit_prices", {})
CATALOG_MAPPING = CATALOG.get("catalog_1c_mapping", {})
SERVICES_LIST = CATALOG.get("services_list", [])
TARIFFS = CATALOG.get("tariffs", {})


@lru_cache(maxsize=128)
def find_best_service_name(raw_name: str) -> str:
    """Шукає найбільш схожу назву з офіційного services_list (з кешуванням)."""
    if not SERVICES_LIST or not raw_name:
        return raw_name

    matches = get_close_matches(raw_name, SERVICES_LIST, n=1, cutoff=0.6)
    return matches[0] if matches else raw_name


def _apply_1c_mapping(item: Dict, category_key: str) -> bool:
    """Берет точные данные для вбивания в 1С прямо из подготовленного JSON."""
    mapping_list = CATALOG_MAPPING.get(category_key, [])
    target_price = item.get("unit_price_for_1c", item.get("price", 0))

    for cat_item in mapping_list:
        if cat_item.get("price") == target_price:
            item["name"] = cat_item["name"]
            item["1c_search_key"] = cat_item.get("search_key", cat_item["name"])
            item["1c_down_presses"] = cat_item.get("dropdown_index", 0)
            return True

    return False


def _process_complex_goods_and_mapping(goods: List[Dict], warnings: List[str]):
    for item in goods:
        name_lower = item.get("name", "").lower()
        item.setdefault("1c_down_presses", 0)

        category = None
        if any(kw in name_lower for kw in ["труна", "гроб"]): category = "coffins"
        elif any(kw in name_lower for kw in ["вінок", "венок"]): category = "wreaths"
        elif any(kw in name_lower for kw in ["хрест", "крест"]): category = "crosses"
        elif "корзина" in name_lower: category = "baskets"
        elif "табличка" in name_lower: category = "plaques"
        elif any(kw in name_lower for kw in ["рушник", "отче"]): category = "towels"

        if category:
            mapped = _apply_1c_mapping(item, category)
            if not mapped:
                item["name"] = find_best_service_name(item["name"])
        else:
            healed = False
            for known_name, valid_prices in KNOWN_UNIT_PRICES.items():
                if known_name.lower() in name_lower:
                    unit_p = valid_prices[0]
                    total_p = item.get("price", 0)
                    qty = item.get("quantity", 1)

                    if qty == 1 and total_p > unit_p and total_p % unit_p == 0:
                        real_qty = total_p // unit_p
                        item["quantity"] = real_qty
                        item["unit_price_for_1c"] = unit_p
                        item["name"] = known_name
                        logger.info(f"🔧 Авто-фікс кількості: {known_name} -> {real_qty} шт по {unit_p} грн")
                    else:
                        item["name"] = known_name

                    healed = True
                    break

            if not healed:
                item["name"] = find_best_service_name(item["name"])


def apply_business_rules_in_python(data: Dict[str, Any], num_addresses: int, booked_in_1c: List[str]) -> Dict[str, Any]:
    services = list(data.get("services", []))
    goods = list(data.get("goods", []))
    transport = list(data.get("transport", []))
    warnings = list(data.get("warnings", []))

    for category in [services, goods, transport]:
        for item in category:
            total_sum = item.get("price", 0)
            qty = max(1, item.get("quantity", 1))
            item["unit_price_for_1c"] = total_sum // qty

    clean_services = []
    total_staff_count = 0
    total_vehicle_count = 0

    kopka_persons = DIGGING_RULES.get("kopka_person_count", 4)
    base_price_per_person = DIGGING_RULES.get("base_price_per_person", 1925)
    base_burial_price = base_price_per_person * kopka_persons

    towel_prices = DIGGING_RULES.get("towel_prices", [1400])
    min_towel_price = min(towel_prices) if towel_prices else 1400

    booked_normalized = {" ".join(name.lower().split()) for name in booked_in_1c}

    for s in services:
        name_lower = s.get("name", "").lower()
        name_normalized = " ".join(name_lower.split())

        if name_normalized in booked_normalized:
            warnings.append(f"Удален дубликат из 1С: {s['name']}")
            continue

        raw_qty = s.get("quantity", 1)
        raw_price = s.get("price", 0)

        if "закопув" in name_lower and raw_price >= (base_burial_price + min_towel_price):
            s["price"] = base_burial_price
            s["quantity"] = 1
            s["unit_price_for_1c"] = base_burial_price
            s["name"] = find_best_service_name(s["name"])
            clean_services.append(s)

            towel_p = raw_price - base_burial_price
            goods.append({
                "name": "Рушник для опускання", "price": towel_p,
                "quantity": 1, "unit_price_for_1c": towel_p
            })
            warnings.append(f"✂️ Разделено: Закопка ({base_burial_price}) и Рушник ({towel_p})")
            continue

        if any(kw in name_lower for kw in ["снос", "персонал", "ескорт"]):
            total_staff_count += raw_qty
            s["name"] = find_best_service_name(s["name"])
            clean_services.append(s)
            continue

        if "церемоніймейстер" in name_lower:
            total_staff_count += 1
            s["name"] = find_best_service_name(s["name"])
            clean_services.append(s)
            continue

        if any(kw in name_lower for kw in ["рушник", "хусточки", "свічки", "набір", "комплект"]):
            goods.append(s)
            continue

        s["name"] = find_best_service_name(s["name"])
        clean_services.append(s)

    for t in transport:
        t["name"] = find_best_service_name(t["name"])
        total_vehicle_count += max(1, t.get("quantity", 1))

    price_extra_staff = TARIFFS.get("extra_point", 500)
    price_extra_trans = TARIFFS.get("transport_base", 1000)

    if num_addresses > 0:
        if total_staff_count > 0:
            qty_extra = total_staff_count * num_addresses
            clean_services.append({
                "name": "снос (доп. точка)", "price": qty_extra * price_extra_staff,
                "quantity": qty_extra, "unit_price_for_1c": price_extra_staff
            })
        if total_vehicle_count > 0:
            qty_trans = total_vehicle_count * num_addresses
            transport.append({
                "name": "доп. точка (транспорт)", "price": qty_trans * price_extra_trans,
                "quantity": qty_trans, "unit_price_for_1c": price_extra_trans
            })

    _process_complex_goods_and_mapping(goods, warnings)

    total = sum(svc.get("price", 0) for svc in clean_services)
    total += sum(gd.get("price", 0) for gd in goods)
    total += sum(tr.get("price", 0) for tr in transport)

    return {
        **data,
        "services": clean_services, "goods": goods, "transport": transport,
        "warnings": warnings, "calculated_total": total
    }


def validate_and_normalize(raw_json_str: str, num_addresses: int, booked_in_1c: List[str], retries: int = 3) -> Dict[str, Any]:
    prompt = f"""
    You are a strict Data Engineer. Normalize item names in JSON.
    RAW DATA: {raw_json_str}
    SERVICES DICTIONARY: {SERVICES_JSON}
    RULES: 1. FIO -> Title Case. 2. Cemeteries -> Match {CEMETERIES_JSON}. 3. Normalize ONLY 'services'.
    Return ONLY clean JSON.
    """
    for attempt in range(retries):
        try:
            # Новый синтаксис
            response = client.models.generate_content(
                model=TEXT_MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0)
            )
            data = safe_parse_json(response.text, expected_type='object')
            if data:
                return apply_business_rules_in_python(data, num_addresses, booked_in_1c)
        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt + 1} не удалась: {e}")
            time.sleep(2)
    return {}