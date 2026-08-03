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

logger = logging.getLogger(__name__)

EDITABLE_TARIFFS = ("extra_point", "transport_base")
EDITABLE_DIGGING_RULES = ("base_price_per_person", "kopka_person_count")


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
    data = _require_catalog()
    if key in EDITABLE_TARIFFS:
        data.setdefault("tariffs", {})[key] = value
    elif key in EDITABLE_DIGGING_RULES:
        data.setdefault("digging_rules", {})[key] = value
    else:
        raise ValueError(f"Неизвестный/не редактируемый ключ тарифа: {key}")

    _persist(data)
    reload_catalog()
    logger.info("💰 Тариф изменён администратором: %s = %s", key, value)
