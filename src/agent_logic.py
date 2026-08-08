import logging
import re
import time
from difflib import SequenceMatcher, get_close_matches
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


# Goods are routed into a `catalog_1c_mapping` category by keyword, first
# match wins — so ORDER MATTERS. "Рушник на хрест" deliberately lands in
# `crosses` rather than `towels` because that's where the real catalog maps
# it; moving `towels` above `crosses` would silently change which 1C
# nomenclature line gets typed. Add new categories here (see
# docs/BUSINESS_LOGIC.md, rule G1).
# `plaques` MUST stay above `crosses`: a real form line is "Табличка на
# хрест", which contains both keywords, and it is a plaque — with crosses
# first it was routed into the crosses list, where no табличка exists, so
# the line could never map.
CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coffins", ("труна", "гроб")),
    ("wreaths", ("вінок", "венок")),
    ("baskets", ("корзина",)),
    ("plaques", ("табличка",)),
    ("crosses", ("хрест", "крест")),
    ("towels", ("рушник", "отче", "полотенце")),
)

# Which mapping category services/transport lines are looked up in.
SERVICE_MAPPING_CATEGORY = "services"

# Below this name-similarity score a price-ambiguous candidate is refused
# rather than guessed at: an unmapped line becomes a visible conflict for
# the operator, while a wrong guess gets typed into 1C silently and there
# is no undo.
AMBIGUOUS_NAME_CUTOFF = 0.45

# A price-only match whose name resembles the catalog entry less than this
# is kept, but marked low-confidence and pushed into the operator's review
# list. Real orders need both behaviours: "Послуги персоналу для поховання"
# -> "снос (Галстук)" is a correct mapping that shares no words, while
# "Насипний пагорб" -> "Перевезення покійного" is pure price coincidence.
# Nothing in the name or the price tells those two apart — only a human or
# an `aliases` entry in catalog.json can, so the pipeline maps them and
# says so instead of pretending to be sure.
NAME_CONFIDENCE_FLOOR = 0.35

# Kept, but flagged for the operator to eyeball: the name matched well
# enough to pick between same-priced candidates, just not exactly.
LOW_CONFIDENCE_REASONS = frozenset({"price_ambiguous_by_name"})

# Last-resort fuzzy matching of a service name against services_list.
FUZZY_NAME_CUTOFF = 0.8
FUZZY_TIE_MARGIN = 0.05

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _norm(name: str) -> str:
    """Case/whitespace-insensitive form used for all name comparisons."""
    return " ".join(str(name or "").lower().split())


def _norm_loose(name: str) -> str:
    """Additionally punctuation-insensitive: "снос-ескорт", "снос (ескорт)"
    and "снос ескорт" all collapse to the same string. Without this,
    Gemini writing a space where the catalog has a hyphen fell through to
    the substring pass and matched the *shorter* "снос", quietly dropping
    the escort surcharge."""
    return " ".join(_PUNCT_RE.sub(" ", _norm(name)).split())


def _load_from_catalog() -> None:
    """(Re)derives the module-level catalog snapshots below from
    `config.CATALOG_DATA` — the single source of truth. Called once at
    import time and again by `refresh_from_catalog()` after the Telegram
    admin panel edits catalog.json, so a running process picks up new
    tariffs/mappings without a restart.
    """
    global SERVICES_JSON, CEMETERIES_JSON, PERSONNEL_PACKAGES
    global DIGGING_RULES, KNOWN_UNIT_PRICES, CATALOG_MAPPING, SERVICES_LIST, TARIFFS
    SERVICES_JSON = config.SERVICES_JSON
    CEMETERIES_JSON = config.CEMETERIES_JSON
    DIGGING_RULES = config.CATALOG_DATA.get("digging_rules", {})
    KNOWN_UNIT_PRICES = config.CATALOG_DATA.get("known_unit_prices", {})
    CATALOG_MAPPING = config.CATALOG_DATA.get("catalog_1c_mapping", {})
    SERVICES_LIST = config.CATALOG_DATA.get("services_list", [])
    TARIFFS = config.CATALOG_DATA.get("tariffs", {})
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

    Порядок пошуку: точний збіг → збіг без розділових знаків ("снос ескорт"
    -> "снос-ескорт") → входження підрядка (типово для скорочень на кшталт
    "Закопування" -> "Закопування/опускання труни") → нечіткий пошук за
    схожістю рядків.
    """
    if not SERVICES_LIST or not raw_name:
        return raw_name

    raw_lower = _norm(raw_name)

    for candidate in SERVICES_LIST:
        if _norm(candidate) == raw_lower:
            return candidate

    raw_loose = _norm_loose(raw_name)
    for candidate in SERVICES_LIST:
        if _norm_loose(candidate) == raw_loose:
            return candidate

    # Two different situations wear the same "substring" shirt and need
    # opposite tie-breaks:
    #
    #   raw ⊂ candidate — the form used an abbreviation ("Закопування" ->
    #     "Закопування/опускання труни"). The shortest candidate is the
    #     least-embellished full name, so prefer short.
    #
    #   candidate ⊂ raw — one form cell holds several services
    #     ("Насипний пагорб/Встанов. хреста"). Preferring short here picked
    #     the most *generic* word in the line: that cell normalized to
    #     "Хрест" — a physical cross from the goods catalog — and its 1200
    #     грн then price-matched to "Перевезення покійного", turning a grave
    #     mound into a corpse transfer. Prefer the longest, most specific
    #     candidate instead.
    contains_raw = [c for c in SERVICES_LIST if raw_lower in _norm(c)]
    if contains_raw:
        return min(contains_raw, key=len)

    inside_raw = [c for c in SERVICES_LIST if _norm(c) in raw_lower]
    if inside_raw:
        return max(inside_raw, key=len)

    # The old 0.6 cutoff was loose enough to rename a line into a different
    # service: "Автотранспорт для поховання" scored 0.72 against
    # "Автотранспорт під вінки" purely on their shared 13-character prefix,
    # and "Закопування" scores 0.667 against "Викопування" — the opposite
    # operation. A genuine OCR typo scores far higher ("Ктафалк" ->
    # "Катафалк" is 0.93), so 0.8 keeps the typos and drops the neighbours.
    matches = get_close_matches(raw_name, SERVICES_LIST, n=2, cutoff=FUZZY_NAME_CUTOFF)
    if not matches:
        return raw_name

    # Two near-equal candidates mean the similarity is coming from a shared
    # prefix rather than from being the same service. Keeping the raw name
    # leaves a line the operator can recognise; picking one invents a
    # service they never wrote down.
    if len(matches) > 1:
        best, runner_up = (
            SequenceMatcher(None, raw_lower, _norm(c)).ratio() for c in matches[:2]
        )
        if best - runner_up < FUZZY_TIE_MARGIN:
            return raw_name
    return matches[0]


def _find_mapping_entry(category_key: str, raw_name: str, target_price: int) -> tuple[dict | None, str]:
    """Picks the one `catalog_1c_mapping` entry a line item refers to.

    Matching is **name-first, price-second**. The original code matched on
    price alone and took the first hit, which is wrong whenever a category
    has two items at the same price — and the real catalog has seven such
    collisions. Concretely: "Отче 350" used to be typed into 1C as "Рушник
    на фото 350", because that entry happens to sit earlier in the towels
    list at the same 350 грн.

    Returns `(entry, reason)`; `entry` is None when nothing matched
    confidently, which leaves the item unmapped and therefore visible as a
    conflict rather than silently mistyped.
    """
    entries = CATALOG_MAPPING.get(category_key) or []
    if not entries:
        return None, "no_category"

    norm = _norm(raw_name)
    loose = _norm_loose(raw_name)
    price_matches = [e for e in entries if e.get("price") == target_price]

    def score(entry: dict) -> tuple[int, float, int]:
        entry_norm = _norm(entry.get("name", ""))
        return (
            1 if entry.get("price") == target_price else 0,
            SequenceMatcher(None, norm, entry_norm).ratio(),
            -len(entry_norm),
        )

    if norm:
        exact = [e for e in entries if _norm(e.get("name", "")) == norm]
        if exact:
            return max(exact, key=score), "name_exact"

        loose_hits = [e for e in entries if _norm_loose(e.get("name", "")) == loose]
        if loose_hits:
            return max(loose_hits, key=score), "name_loose"

        # `aliases` is how the catalog records a mapping that no amount of
        # string comparison could infer — the form's wording and the 1C
        # nomenclature simply differ ("Послуги персоналу для поховання" is
        # entered as "снос (Галстук)"). Ranked by `score`, so several
        # entries can share an alias and price picks the right variant.
        alias_hits = [
            e for e in entries
            if any(_norm_loose(a) == loose for a in e.get("aliases", []) or [])
        ]
        if alias_hits:
            return max(alias_hits, key=score), "alias"

        # Catalog names carry a trailing gloss — "Отче 350 (Отче 75 Рушник
        # на крышку гроба)" — so what the form says is typically a substring
        # of the catalog name. Restrict to the right price bucket when we
        # have one, so "Отче" can't match "Отче 220" on a 350 грн line.
        #
        # A priceless line ("Труна — б/ч", i.e. supplied by the family) has
        # no bucket to restrict to, and a bare "Труна" is a substring of
        # over a hundred coffin names. Matching it would pick one of them
        # essentially at random, so partial matching is off without a price
        # — an exact name or an alias is still honoured.
        if target_price > 0:
            pool = price_matches or entries
            contained = [
                e for e in pool
                if norm in _norm(e.get("name", "")) or _norm(e.get("name", "")) in norm
            ]
            if contained:
                return max(contained, key=score), "name_substring"

    if len(price_matches) == 1:
        entry = price_matches[0]
        similarity = SequenceMatcher(None, norm, _norm(entry.get("name", ""))).ratio()
        if norm and similarity < NAME_CONFIDENCE_FLOOR:
            return entry, "price_unique_low"
        return entry, "price_unique"

    if len(price_matches) > 1:
        best = max(price_matches, key=score)
        if SequenceMatcher(None, norm, _norm(best.get("name", ""))).ratio() >= AMBIGUOUS_NAME_CUTOFF:
            return best, "price_ambiguous_by_name"
        return None, "price_ambiguous"

    return None, "no_match"


def _apply_1c_mapping(item: dict, category_key: str, warnings: list[str] | None = None) -> bool:
    """Берет точные данные для вбивания в 1С прямо из подготовленного JSON."""
    target_price = item.get("unit_price_for_1c", item.get("price", 0))
    raw_name = item.get("name", "")

    entry, reason = _find_mapping_entry(category_key, raw_name, target_price)
    if entry is None:
        if reason == "price_ambiguous" and warnings is not None:
            warnings.append(
                f"❓ '{raw_name}' ({target_price} грн): в каталоге несколько позиций с такой ценой "
                "и название не подошло ни к одной — впиши в 1С вручную"
            )
        return False

    # Sharing a price with exactly one catalog line, while sharing no words
    # with it, is not evidence — it's coincidence until a human says
    # otherwise. The base order proves both directions at once: "Послуги
    # персоналу для поховання" -> "снос (Галстук)" is genuinely correct,
    # and "Насипний пагорб" -> "Перевезення покійного" is a 1200 грн
    # accident that would have buried a grave-mound line in 1C as a corpse
    # transfer. Nothing distinguishes them mechanically, so this refuses
    # both and points at `aliases`, which records the real ones once.
    if reason == "price_unique_low":
        if warnings is not None:
            warnings.append(
                f"❓ '{raw_name}' ({target_price} грн): по цене подходит '{entry['name']}', "
                "но названия не похожи — впиши в 1С вручную. Если связка верная, добавь "
                f"\"aliases\": [\"{raw_name}\"] к этой позиции в catalog.json"
            )
        return False

    # A partial name match that lands on a *different* price is almost
    # always the wrong variant of the right product family, not a discount.
    # Real case: a form line "Рушник на хрест 380" substring-matches both
    # "Рушник на хрест 250" and "Рушник на хрест 350" — neither is 380, and
    # picking either types a wrong nomenclature line into 1C. An exact name
    # match is exempt: there the operator genuinely repriced a known item.
    entry_price = entry.get("price", 0)
    if reason == "name_substring" and target_price and entry_price != target_price:
        if warnings is not None:
            warnings.append(
                f"❓ '{raw_name}' за {target_price} грн: в каталоге похожая позиция "
                f"'{entry['name']}' стоит {entry_price} грн — цена не совпала, впиши в 1С вручную"
            )
        return False

    if not entry.get("search_key"):
        logger.warning("Позиция '%s' в каталоге без search_key — ввести в 1С автоматически нельзя", entry["name"])
        return False

    item["name"] = entry["name"]
    item["1c_search_key"] = entry["search_key"]
    item["1c_down_presses"] = entry.get("dropdown_index", 0)
    item["1c_match_reason"] = reason

    if reason in LOW_CONFIDENCE_REASONS:
        item["1c_match_confidence"] = "low"
        if warnings is not None:
            warnings.append(
                f"🔁 '{raw_name}' ({target_price} грн) сопоставлено ТОЛЬКО по цене как "
                f"'{entry['name']}' — названия не похожи, проверь перед вводом "
                "(закрепить связку можно через aliases в catalog.json)"
            )
    elif reason == "price_unique" and warnings is not None and _norm(raw_name) != _norm(entry["name"]):
        warnings.append(f"🔁 '{raw_name}' сопоставлено по цене как '{entry['name']}' — проверь")
    return True


def _process_complex_goods_and_mapping(goods: list[dict], warnings: list[str]) -> None:
    for item in goods:
        name_lower = _norm(item.get("name", ""))
        item.setdefault("1c_down_presses", 0)

        category = next(
            (cat for cat, keywords in CATEGORY_KEYWORDS if any(kw in name_lower for kw in keywords)),
            None,
        )

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


def _apply_personnel_package(service: dict, warnings: list[str]) -> bool:
    """Decodes a bundled снос/ескорт price into people × per-person tariff.

    `personnel_packages` maps a *total* to a crew: "6200" is 4 people of
    «снос» at 1550 each. Forms are written both ways — sometimes "снос 4 ×
    1550", sometimes just "снос 6200" — and until now the second form went
    into 1C as a single line of 6200 грн, which is not a nomenclature price
    that exists, so it never matched the catalog.

    Only fires on an exact total match, and only corrects the quantity when
    it actually disagrees, so a form that already spells out the crew size
    is left untouched.
    """
    if not PERSONNEL_PACKAGES:
        return False

    package = PERSONNEL_PACKAGES.get(str(service.get("price", 0)))
    if not package:
        return False

    qty = int(package.get("qty", 0) or 0)
    if qty <= 0:
        return False

    total = service.get("price", 0)
    if total % qty:
        logger.warning("Пакет персонала %s не делится на %s чел. — пропускаю", total, qty)
        return False

    current_qty = service.get("quantity", 1)
    if current_qty == qty:
        service["unit_price_for_1c"] = total // qty
        return False

    service["quantity"] = qty
    service["unit_price_for_1c"] = total // qty
    if package.get("name"):
        service["name"] = package["name"]
    warnings.append(
        f"👥 Пакет {total} грн распознан как {package.get('name', 'персонал')} × {qty} чел. "
        f"по {total // qty} грн (в бланке было {current_qty})"
    )
    logger.info("👥 Пакет персонала %s грн -> %s x%s", total, package.get("name"), qty)
    return True


def _map_service_lines(items: list[dict], warnings: list[str]) -> None:
    """Attaches 1C search keys to services/transport lines.

    `catalog_1c_mapping.services` existed in catalog.json from the start but
    nothing ever read it — only goods were mapped. The effect was that
    *every* service and transport line reached `onec_order_entry` without a
    `1c_search_key`, which that module (correctly) refuses to type, so
    `OrderSummary.can_auto_enter` was False on every single order. Mapping
    them here is what makes automatic entry actually reachable.
    """
    for item in items:
        item.setdefault("1c_down_presses", 0)
        if item.get("1c_search_key"):
            continue
        _apply_1c_mapping(item, SERVICE_MAPPING_CATEGORY, warnings)


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
            _apply_personnel_package(s, warnings)
            total_staff_count += max(1, s.get("quantity", 1))
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
    _map_service_lines(clean_services, warnings)
    _map_service_lines(transport, warnings)

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
