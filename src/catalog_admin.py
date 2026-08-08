"""Safe, narrow write-path for the Telegram admin panel to adjust
catalog.json's numeric business rules (tariffs, digging rules) without
hand-editing JSON. Deliberately does NOT expose editing services_list or
catalog_1c_mapping here — those define the 1C search keys/dropdown
indices and are risky to edit from a chat UI; change catalog.json directly
and run `/reload_catalog`.
"""

from __future__ import annotations

import json
import logging

from src import agent_logic, config
from src.errors import CatalogLoadError
from src.settings import get_settings
from src.utils import MAX_PG_INT

logger = logging.getLogger(__name__)

EDITABLE_TARIFFS = ("extra_point", "transport_base")
EDITABLE_DIGGING_RULES = ("base_price_per_person", "kopka_person_count")

# kopka_person_count is a headcount multiplied into base_burial_price
# (agent_logic.apply_business_rules_in_python); a typo'd 0 there doesn't
# crash — it makes base_burial_price 0, so the digging/towel split rule
# (S2) fires on almost any закопка line, silently reclassifying most of it
# as "towel" on every order until someone notices the price list looks
# wrong. Everything else here can legitimately be 0 (e.g. no surcharge
# for extra points), just never negative or absurdly large.
_STRICTLY_POSITIVE = frozenset({"kopka_person_count"})


def _require_catalog() -> dict:
    if not config.CATALOG_DATA:
        raise CatalogLoadError(
            "catalog.json ещё не загружен — сначала помести настоящий файл в data/catalog.json "
            "и вызови /reload_catalog"
        )
    return config.CATALOG_DATA


def _persist(data: dict) -> None:
    path = get_settings().catalog_path
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_from_disk() -> dict:
    """Re-reads catalog.json rather than persisting the in-memory copy.

    The in-memory catalog is a *sanitized* view — `config._load_catalog()`
    drops entries that fail the schema so they can't crash the pipeline.
    Writing that copy back would make an unrelated `/set_tariff` silently
    delete those lines from the operator's price list. Editing the file
    on disk keeps every line the operator wrote, including the broken ones
    they still need to fix.
    """
    path = get_settings().catalog_path
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise CatalogLoadError(
            f"Не удалось прочитать {path} для правки: {e}. Проверь файл и вызови /reload_catalog."
        ) from e
    if not isinstance(data, dict):
        raise CatalogLoadError(f"{path} не является JSON-объектом — правка тарифов невозможна.")
    return data


def reload_catalog() -> dict:
    data = config.reload_catalog()
    agent_logic.refresh_from_catalog()
    logger.info("🔄 Каталог перезагружен из %s", get_settings().catalog_path)
    return data


def get_editable_tariffs() -> dict[str, int]:
    data = config.CATALOG_DATA
    result = {key: data.get("tariffs", {}).get(key) for key in EDITABLE_TARIFFS}
    result.update({key: data.get("digging_rules", {}).get(key) for key in EDITABLE_DIGGING_RULES})
    return result


def update_tariff(key: str, value: int) -> None:
    _require_catalog()

    if key in EDITABLE_TARIFFS:
        section = "tariffs"
    elif key in EDITABLE_DIGGING_RULES:
        section = "digging_rules"
    else:
        raise ValueError(f"Неизвестный/не редактируемый ключ тарифа: {key}")

    # A typo an admin can make with one keystroke (missing digit, stray
    # minus) used to be accepted silently and corrupt every subsequent
    # order's math with no feedback until someone noticed the price list
    # looked wrong — worse for kopka_person_count (see the comment above)
    # than for the others, but none of them make sense negative.
    floor = 1 if key in _STRICTLY_POSITIVE else 0
    if not (floor <= value <= MAX_PG_INT):
        raise ValueError(
            f"{key} должен быть {'положительным' if floor else 'неотрицательным'} "
            f"числом не больше {MAX_PG_INT}, получено {value}"
        )

    data = _read_from_disk()
    if not isinstance(data.get(section), dict):
        data[section] = {}
    data[section][key] = value

    _persist(data)
    reload_catalog()
    logger.info("💰 Тариф изменён администратором: %s = %s", key, value)
