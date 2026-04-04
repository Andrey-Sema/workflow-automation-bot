import json
import time
import logging
from typing import Dict, List, Any
from pathlib import Path
import google.generativeai as genai

# Configuration imports
from src.config import SERVICES_JSON, CEMETERIES_JSON, TEXT_MODEL_NAME
from src.utils import safe_parse_json

logger = logging.getLogger(__name__)
TEXT_MODEL = genai.GenerativeModel(TEXT_MODEL_NAME)

# --- LOAD KNOWLEDGE BASE (CATALOG.JSON) ---
BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"

try:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        CATALOG = json.load(f)
    logger.info("✅ 1C Catalog successfully loaded into memory.")
except Exception as e:
    logger.critical(f"❌ FATAL: Failed to load data/catalog.json! {e}")
    CATALOG = {}

# Extract required blocks from catalog
PRICES = CATALOG.get("tariffs", {})
DIGGING_RULES = CATALOG.get("digging_rules", {})
RAW_PERSONNEL = CATALOG.get("personnel_packages", {})

# Convert tariff keys from strings to integers for fast lookup
PERSONNEL_TARIFFS = {int(k): v for k, v in RAW_PERSONNEL.items()}
KNOWN_UNIT_PRICES = CATALOG.get("known_unit_prices", {})
CATALOG_MAPPING = CATALOG.get("catalog_1c_mapping", {})


def _apply_1c_mapping(item: Dict, category_key: str) -> bool:
    """
    1C Magic: Replaces the generic item name with the exact 1C catalog name
    based on price, and calculates the required 'Arrow Down' keystrokes.
    """
    mapping_list = CATALOG_MAPPING.get(category_key, [])
    target_price = item.get("price", 0)

    match_index = -1
    for i, cat_item in enumerate(mapping_list):
        if cat_item["price"] == target_price:
            match_index = i
            break

    if match_index != -1:
        matched_item = mapping_list[match_index]
        exact_name = matched_item["name"]

        # Calculate how many times this exact name appeared BEFORE our index
        # This tells the 1C bot how many times to press 'Arrow Down'
        down_presses = sum(1 for i in range(match_index) if mapping_list[i]["name"] == exact_name)

        item["name"] = exact_name
        item["1c_down_presses"] = down_presses
        return True

    return False


def _process_complex_goods_and_mapping(goods: List[Dict], warnings: List[str]):
    """
    Processes complex goods (coffins, crosses) and simple ones (candles)
    against the 1C catalog to normalize names and fix AI math errors.
    """
    for item in goods:
        name_lower = item.get("name", "").lower()
        price = item.get("price", 0)
        qty = item.get("quantity", 1)
        item["1c_down_presses"] = 0  # Default to zero keystrokes

        # 1. Attempt to map complex goods to 1C exact names
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
            continue

        # 2. Process minor items (Handkerchiefs, Candles) if not mapped above
        for known_name, valid_prices in KNOWN_UNIT_PRICES.items():
            if known_name.lower() in name_lower:
                # If the price is already a known valid price, just normalize the name
                if price in valid_prices:
                    item["name"] = known_name
                    break
                # If AI multiplied unit price by quantity, revert it to unit price
                elif qty > 1 and price % qty == 0 and (price // qty) in valid_prices:
                    item["price"] = price // qty
                    item["name"] = known_name
                    warnings.append(
                        f"⚠️ Catalog Math Fix: Price for '{known_name}' divided by {qty}. New unit price: {item['price']}"
                    )
                    break


def apply_business_rules_in_python(data: Dict[str, Any], num_addresses: int, booked_in_1c: List[str]) -> Dict[str, Any]:
    # 1. Protection against mutation: shallow copies
    services = list(data.get("services", []))
    goods = list(data.get("goods", []))
    transport = list(data.get("transport", []))
    warnings = list(data.get("warnings", []))

    clean_services = []
    total_staff_count = 0
    total_vehicle_count = 0

    # Dynamic pricing from catalog
    base_burial_price = DIGGING_RULES.get("base_price_complete_4_pax", 6400)
    # Ищем минимальную цену рушника из массива (по умолчанию 1400)
    towel_prices = DIGGING_RULES.get("towel_prices", [1400])
    min_towel_price = min(towel_prices) if towel_prices else 1400

    # 2. Deterministic Blacklist (Exact normalized match)
    booked_normalized = {" ".join(name.lower().split()) for name in booked_in_1c}

    # --- PASS 1: Normalize Services, Split Items, Count Participants ---
    for s in services:
        name_lower = s.get("name", "").lower()
        name_normalized = " ".join(name_lower.split())

        # Check against 1C duplicates (Exact match)
        if name_normalized in booked_normalized:
            logger.info(f"🚫 Removing duplicate already in 1C: {s['name']}")
            warnings.append(f"Удален дубликат из 1С: {s['name']}")
            continue

        raw_qty = max(1, s.get("quantity", 1))
        raw_price = s.get("price", 0)

        # BURIAL & TOWEL SPLIT (Fully dynamic)
        if "закопув" in name_lower and raw_price >= (base_burial_price + min_towel_price):
            s["price"] = base_burial_price
            s["quantity"] = 1
            clean_services.append(s)

            towel_price = raw_price - base_burial_price
            goods.append({"name": "Рушник для опускання", "price": towel_price, "quantity": 1})
            warnings.append(f"✂️ Split: Separated Towel ({towel_price}) from Burial total.")
            continue

        # PERSONNEL / SNOS (Safe integer division)
        if any(kw in name_lower for kw in ["снос", "персонал", "ескорт"]):
            total_staff_count += raw_qty

            if raw_price % raw_qty != 0:
                warnings.append(f"⚠️ Нечетная сумма для персонала: {raw_price} / {raw_qty}")

            s["price"] = raw_price // raw_qty
            s["quantity"] = raw_qty
            clean_services.append(s)
            logger.info(f"⚙️ SNOS Normalized: Divided {raw_price} by {raw_qty} pax. Unit price: {s['price']}")
            continue

        # CEREMONYMASTER
        if "церемоніймейстер" in name_lower:
            total_staff_count += 1
            clean_services.append(s)
            continue

        # MIGRATE MISPLACED GOODS
        if any(item_kw in name_lower for item_kw in ["рушник", "хусточки", "свічки", "набір", "комплект"]):
            goods.append(s)
            logger.info(f"📦 Migration: Moved item '{s['name']}' from Services to Goods.")
            continue

        clean_services.append(s)

    for t in transport:
        total_vehicle_count += max(1, t.get("quantity", 1))

    # --- PASS 2: Handling Extra Points ---
    if num_addresses > 0:
        if total_staff_count > 0:
            staff_extra_qty = total_staff_count * num_addresses
            clean_services.append({"name": "снос (доп. точка)", "price": 400, "quantity": staff_extra_qty})
            warnings.append(f"📍 Added {staff_extra_qty} extra points for personnel (400 UAH/pax).")

        if total_vehicle_count > 0:
            transport_extra_qty = total_vehicle_count * num_addresses
            transport.append({"name": "доп. точка (транспорт)", "price": 1000, "quantity": transport_extra_qty})
            warnings.append(f"📍 Added {transport_extra_qty} extra points for vehicles (1000 UAH/veh).")

    # --- PASS 3: Apply 1C Mapping ---
    _process_complex_goods_and_mapping(goods, warnings)

    # --- FINAL DATA ASSEMBLY (No mutation of original 'data') ---
    total = sum(svc["price"] * svc.get("quantity", 1) for svc in clean_services)
    total += sum(gd["price"] * gd.get("quantity", 1) for gd in goods)
    total += sum(tr["price"] * tr.get("quantity", 1) for tr in transport)

    return {
        **data,  # Распаковываем старые данные (customer, deceased, handwritten_total)
        "services": clean_services,  # Перезаписываем массивы
        "goods": goods,
        "transport": transport,
        "warnings": warnings,
        "calculated_total": total
    }


def validate_and_normalize(raw_json_str: str, num_addresses: int, booked_in_1c: List[str], retries: int = 3) -> Dict[
    str, Any]:
    """
    Agent 2 Pipeline: Normalizes raw JSON names via LLM and applies strict business logic.
    """
    logger.info(f"🧠 AGENT 2: Starting data normalization. Extra addresses: {num_addresses}")

    # Notice: Blacklist rules removed from prompt, logic entirely handled in Python
    prompt = f"""
    You are a strict Data Engineer. Your task is to normalize item names in the JSON.
    RAW DATA: {raw_json_str}
    SERVICES DICTIONARY: {SERVICES_JSON}

    RULES:
    1. Full names (FIO) -> Title Case. 
    2. Cemeteries -> Must strictly match the list {CEMETERIES_JSON}.
    3. CRITICAL: Keep "goods", "transport" arrays and "handwritten_total" field UNCHANGED! Normalize ONLY "services".

    Return ONLY a clean JSON object. No markdown, no explanations.
    """

    for attempt in range(retries):
        try:
            # TEXT_MODEL is using generation_config, temperature=0.0 is fine for strict formatting tasks
            response = TEXT_MODEL.generate_content(prompt, generation_config={"temperature": 0.0})
            data = safe_parse_json(response.text, expected_type='object')

            if data:
                return apply_business_rules_in_python(data, num_addresses, booked_in_1c)

        except Exception as e:
            logger.warning(f"⚠️ Normalization attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    return {}