"""Catalog loading: the single source of truth for services, tariffs and
1C mapping data (`data/catalog.json`), plus the (mostly static) cemetery
synonym map.

Only this module reads `catalog.json` from disk — every other module
(`agent_logic`, the Telegram admin panel, etc.) imports the values from
here so there is exactly one in-memory copy and one reload path.
"""

from __future__ import annotations

import json
import logging

from src.settings import get_settings

logger = logging.getLogger(__name__)

# --- AI MODEL NAMES (kept here for backwards-compat imports; source of truth is Settings) ---
_settings = get_settings()
VISION_MODEL_NAME = _settings.vision_model_name
TEXT_MODEL_NAME = _settings.text_model_name
BOOKED_MODEL_NAME = _settings.booked_model_name

# Кладбища: технический маппинг синонимов (не бизнес-данные каталога, поэтому не в JSON)
CEMETERIES_DICT = {
    "Західне": "Западное", "Западное": "Западное",
    "Крематор": "Крематорий", "Крематорий": "Крематорий",
    "2-е христианское": "2-е христианское", "2-е Християнське": "2-е христианское",
    "3-е еврейское": "3-е еврейское",
    "Н.гор": "Новогородское", "Новогородское": "Новогородское", "н. гор": "Новогородское",
    "Северное": "Северное", "Крыжановка": "Крыжановка", "Южное": "Южное",
    "р.с": "Слободское", "Кривая балка": "Кривая балка", "Троицкое": "Троицкое",
    "Латовка": "Латовка", "Усатово": "Усатово", "Черноморка": "Черноморка",
    "Дм. Донского": "Дм. Донского", "Лески": "Лески", "Фонтанка": "Фонтанка",
    "Корсунцы": "Корсунцы", "Красноселка": "Красноселка", "Ильичанка": "Ильичанка",
    "Александровка": "Александровка", "Светлое": "Светлое", "Нерубайское": "Нерубайское",
    "Великая балка": "Великая балка", "Холодная балка": "Холодная балка",
    "Авангард": "Авангард", "Хлебодарское": "Хлебодарское", "Великий дальник": "Великий дальник",
    "Дачное": "Дачное", "Лиманка": "Лиманка", "Сухой Лиман": "Сухой Лиман",
    "Прилиманское": "Прилиманское", "Новая Долина": "Новая Долина",
    "Бурлачья балка": "Бурлачья балка", "Малодолинское": "Малодолинское",
    "Черноморск": "Черноморск", "Овидиополь": "Овидиополь", "Беляевка": "Беляевка",
    "Санжейка": "Санжейка", "Грибовка": "Грибовка", "Маяки": "Маяки",
    "Выгода": "Выгода", "Визирка": "Визирка", "Доброслав": "Доброслав",
}


def _load_catalog() -> dict:
    path = _settings.catalog_path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("⚠️ catalog.json не найден по пути %s. Работаю с пустым каталогом.", path)
        return {}
    except json.JSONDecodeError as e:
        logger.error("⚠️ catalog.json повреждён (невалидный JSON): %s", e)
        return {}


CATALOG_DATA = _load_catalog()
SERVICES_LIST = CATALOG_DATA.get("services_list", [])
SERVICES_JSON = json.dumps(SERVICES_LIST, ensure_ascii=False)
CEMETERIES_JSON = json.dumps(CEMETERIES_DICT, ensure_ascii=False)


def catalog_is_loaded() -> bool:
    """Whether catalog.json was found and parsed. Used as a startup health check."""
    return bool(CATALOG_DATA)


def reload_catalog() -> dict:
    """Re-read catalog.json from disk (used by the Telegram admin panel after edits)."""
    global CATALOG_DATA, SERVICES_LIST, SERVICES_JSON
    CATALOG_DATA = _load_catalog()
    SERVICES_LIST = CATALOG_DATA.get("services_list", [])
    SERVICES_JSON = json.dumps(SERVICES_LIST, ensure_ascii=False)
    return CATALOG_DATA
