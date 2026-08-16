import logging
import re
import time
from difflib import get_close_matches
from functools import lru_cache
from typing import Any

from google.genai import types

from src import config
from src.circuit_breaker import gemini_circuit_breaker
from src.errors import GeminiUnavailableError
from src.gemini_client import get_client
from src.metrics import GEMINI_CALL_DURATION, GEMINI_CALLS
from src.utils import safe_parse_json

logger = logging.getLogger(__name__)

TEXT_MODEL_NAME = config.TEXT_MODEL_NAME


def _load_from_catalog() -> None:
    """(Re)derives the module-level catalog snapshots below from
    `config.CATALOG_DATA` — the single source of truth. Called once at
    import time and again by `refresh_from_catalog()` after the Telegram
    admin panel edits catalog.json, so a running process picks up new
    tariffs/mappings without a restart.
    """
    global SERVICES_JSON, CEMETERIES_JSON
    global DIGGING_RULES, KNOWN_UNIT_PRICES, CATALOG_MAPPING, SERVICES_LIST, TARIFFS, PERSONNEL_PACKAGES
    SERVICES_JSON = config.SERVICES_JSON
    CEMETERIES_JSON = config.CEMETERIES_JSON
    DIGGING_RULES = config.CATALOG_DATA.get("digging_rules", {})
    KNOWN_UNIT_PRICES = config.CATALOG_DATA.get("known_unit_prices", {})
    CATALOG_MAPPING = config.CATALOG_DATA.get("catalog_1c_mapping", {})
    SERVICES_LIST = config.CATALOG_DATA.get("services_list", [])
    TARIFFS = config.CATALOG_DATA.get("tariffs", {})
    # {"<общая сумма пакета>": {"name": "снос"/"снос-ескорт", "qty": N}} —
    # позволяет распознать фиксованный пакет персонала (снос вчетвером,
    # ескорт вшестером ...) по одной лишь сумме на бланке и восстановить
    # реальное к-ть/цену за штуку перед сопоставлением с 1С.
    PERSONNEL_PACKAGES = config.CATALOG_DATA.get("personnel_packages", {})


_load_from_catalog()


def refresh_from_catalog() -> None:
    """Call after `config.reload_catalog()` to pick up edits made through
    the Telegram admin panel (tariffs, digging rules, ...) without
    restarting the process."""
    _load_from_catalog()
    find_best_service_name.cache_clear()


@lru_cache(maxsize=128)
def find_best_service_name(raw_name: str) -> str:
    """Шукає найбільш схожу назву з офіційного services_list (з кешуванням).

    Порядок пошуку: точний збіг → входження підрядка (типово для скорочень
    на кшталт "Закопування" -> "Закопування/опускання труни") → нечіткий
    пошук за схожістю рядків.
    """
    if not SERVICES_LIST or not raw_name:
        return raw_name

    raw_lower = raw_name.lower().strip()

    for candidate in SERVICES_LIST:
        if candidate.lower() == raw_lower:
            return candidate

    substring_matches = [c for c in SERVICES_LIST if raw_lower in c.lower() or c.lower() in raw_lower]
    if substring_matches:
        return min(substring_matches, key=len)

    matches = get_close_matches(raw_name, SERVICES_LIST, n=1, cutoff=0.6)
    return matches[0] if matches else raw_name


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _digit_confusable(a: int, b: int) -> bool:
    """True when `a` and `b` have the same number of digits and differ in
    exactly one digit position — the classic handwritten-OCR misread
    (Vision reading "5650" off the form instead of the actual "3650")."""
    sa, sb = str(a), str(b)
    if len(sa) != len(sb) or sa == sb:
        return False
    return sum(x != y for x, y in zip(sa, sb, strict=True)) == 1


def _apply_1c_mapping(item: dict, category_key: str, warnings: list[str] | None = None) -> bool:
    """Берет точные данные для вбивания в 1С прямо из подготовленного JSON.

    Несколько карточок 1С могут стоить одинаково (например, "снос" за
    500 грн есть и как "Завантаження/розвантаження", и как "Персонал
    (ДОП ТОЧКА/ГОДИНА)") — при неоднозначности по цене выбираем ту
    карточку, чьё название сильнее пересекается по словам с исходным
    названием строки, вместо произвольного первого совпадения.

    Если точной цены в каталоге нет вообще, НИКОГДА не подставляем
    ближайшую по значению карточку молча — цена решает, что вбивается в
    1С, и угадывать её нельзя. Вместо этого, если рядом (`warnings`
    передан), ищем в той же категории цены, отличающиеся от указанной
    ровно на одну цифру (5650 вместо 3650 и т.п.), и добавляем явное
    предупреждение оператору — чтобы он перепроверил бланк, а не полагался
    на то, что "нет соответствия" само по себе укажет на опечатку.
    """
    mapping_list = CATALOG_MAPPING.get(category_key, [])
    target_price = item.get("unit_price_for_1c", item.get("price", 0))
    candidates = [c for c in mapping_list if c.get("price") == target_price]

    if not candidates:
        if warnings is not None:
            suspects = sorted(
                (c for c in mapping_list if _digit_confusable(target_price, c.get("price", 0))),
                key=lambda c: abs(c["price"] - target_price),
            )[:3]
            if suspects:
                variants = ", ".join(f"«{c['name']}» ({c['price']} грн)" for c in suspects)
                warnings.append(
                    f"🔎 Похоже на ошибку распознавания цифр: «{item.get('name', '?')}» — "
                    f"{target_price} грн, такой цены нет в каталоге 1С, но есть {variants}. "
                    f"Сверь цену на бланке вручную!"
                )
        return False

    if len(candidates) > 1:
        item_tokens = _tokenize(item.get("name", ""))
        candidates = sorted(
            candidates,
            key=lambda c: len(item_tokens & _tokenize(c["name"])),
            reverse=True,
        )

    cat_item = candidates[0]
    item["name"] = cat_item["name"]
    item["1c_search_key"] = cat_item.get("search_key", cat_item["name"])
    item["1c_down_presses"] = cat_item.get("dropdown_index", 0)
    return True


def _process_complex_goods_and_mapping(goods: list[dict], warnings: list[str]) -> None:
    for item in goods:
        name_lower = item.get("name", "").lower()
        item.setdefault("1c_down_presses", 0)

        category = None
        if any(kw in name_lower for kw in ["труна", "гроб"]):
            category = "coffins"
        elif any(kw in name_lower for kw in ["вінок", "венок"]):
            category = "wreaths"
        elif any(kw in name_lower for kw in ["хрест", "крест"]):
            category = "crosses"
        elif "корзина" in name_lower:
            category = "baskets"
        elif "табличка" in name_lower:
            category = "plaques"
        elif any(kw in name_lower for kw in ["рушник", "отче"]):
            category = "towels"

        if category:
            mapped = _apply_1c_mapping(item, category, warnings)
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


def apply_business_rules_in_python(data: dict[str, Any], num_addresses: int, booked_in_1c: list[str]) -> dict[str, Any]:
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
            # Написанная на бланке общая сумма (6200, 9600, ...) часто
            # соответствует готовому пакету персонала — восстанавливаем
            # реальные к-ть/цену за штуку по personnel_packages раньше,
            # чем считаем total_staff_count и ищем карточку в 1С.
            package = PERSONNEL_PACKAGES.get(str(int(raw_price)))
            if package:
                raw_qty = package["qty"]
                s["quantity"] = raw_qty
                s["unit_price_for_1c"] = raw_price // raw_qty
                s["name"] = package["name"]

            total_staff_count += raw_qty
            if not _apply_1c_mapping(s, "services", warnings):
                s["name"] = find_best_service_name(s["name"])
            clean_services.append(s)
            continue

        if "церемоніймейстер" in name_lower:
            total_staff_count += 1
            if not _apply_1c_mapping(s, "services", warnings):
                s["name"] = find_best_service_name(s["name"])
            clean_services.append(s)
            continue

        if any(kw in name_lower for kw in ["рушник", "хусточки", "свічки", "набір", "комплект"]):
            goods.append(s)
            continue

        if not _apply_1c_mapping(s, "services", warnings):
            s["name"] = find_best_service_name(s["name"])
        clean_services.append(s)

    for t in transport:
        if not _apply_1c_mapping(t, "services", warnings):
            t["name"] = find_best_service_name(t["name"])
        total_vehicle_count += max(1, t.get("quantity", 1))

    price_extra_staff = TARIFFS.get("extra_point", 500)
    price_extra_trans = TARIFFS.get("transport_base", 1000)

    if num_addresses > 0:
        if total_staff_count > 0:
            qty_extra = total_staff_count * num_addresses
            extra_staff = {
                "name": "снос (доп. точка)", "price": qty_extra * price_extra_staff,
                "quantity": qty_extra, "unit_price_for_1c": price_extra_staff
            }
            _apply_1c_mapping(extra_staff, "services")
            clean_services.append(extra_staff)
        if total_vehicle_count > 0:
            qty_trans = total_vehicle_count * num_addresses
            extra_trans = {
                "name": "доп. точка (транспорт)", "price": qty_trans * price_extra_trans,
                "quantity": qty_trans, "unit_price_for_1c": price_extra_trans
            }
            _apply_1c_mapping(extra_trans, "services")
            transport.append(extra_trans)

    _process_complex_goods_and_mapping(goods, warnings)

    total = sum(svc.get("price", 0) for svc in clean_services)
    total += sum(gd.get("price", 0) for gd in goods)
    total += sum(tr.get("price", 0) for tr in transport)

    return {
        **data,
        "services": clean_services, "goods": goods, "transport": transport,
        "warnings": warnings, "calculated_total": total
    }


def validate_and_normalize(
    raw_json_str: str, num_addresses: int, booked_in_1c: list[str], retries: int = 3
) -> dict[str, Any]:
    if gemini_circuit_breaker.is_open:
        logger.error("🚨 Circuit breaker открыт: Gemini недавно стабильно падал, пропускаю попытки.")
        raise GeminiUnavailableError()

    prompt = f"""
    You are a strict Data Engineer. Normalize item names in JSON.
    RAW DATA: {raw_json_str}
    SERVICES DICTIONARY: {SERVICES_JSON}
    RULES: 1. FIO -> Title Case. 2. Cemeteries -> Match {CEMETERIES_JSON}. 3. Normalize ONLY 'services'.
    Return ONLY clean JSON.
    """
    client = get_client()
    for attempt in range(retries):
        try:
            call_start = time.time()
            response = client.models.generate_content(
                model=TEXT_MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
            )
            GEMINI_CALL_DURATION.labels(agent="logic").observe(time.time() - call_start)
            data = safe_parse_json(response.text, expected_type='object')
            if data:
                GEMINI_CALLS.labels(agent="logic", outcome="success").inc()
                gemini_circuit_breaker.record_success()
                return apply_business_rules_in_python(data, num_addresses, booked_in_1c)
            GEMINI_CALLS.labels(agent="logic", outcome="error").inc()
            gemini_circuit_breaker.record_failure()
        except Exception as e:
            GEMINI_CALLS.labels(agent="logic", outcome="error").inc()
            gemini_circuit_breaker.record_failure()
            logger.warning(f"⚠️ Попытка {attempt + 1} не удалась: {e}")
            time.sleep(2)
    return {}
