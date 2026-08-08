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

from src.catalog_schema import CatalogReport, sanitize_catalog, validate_catalog
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
            data = json.load(f)
    except FileNotFoundError:
        logger.error("⚠️ catalog.json не найден по пути %s. Работаю с пустым каталогом.", path)
        return {}
    except json.JSONDecodeError as e:
        logger.error("⚠️ catalog.json повреждён (невалидный JSON): %s", e)
        return {}

    _log_catalog_report(validate_catalog(data), path)
    # Публикуем очищенную копию, а не сырую: репортить о битой позиции и
    # тут же отдать её в agent_logic означало бы падение посреди наряда.
    return sanitize_catalog(data)


def _log_catalog_report(report: CatalogReport, path: object) -> None:
    """Surfaces catalog problems at load time rather than mid-order.

    Errors are logged but not raised: a partly-broken catalog still lets
    operators work on the lines it does cover, and refusing to start would
    take the whole bot down over one malformed price.
    """
    for issue in report.errors:
        logger.error("catalog %s: %s", path, issue)
    for issue in report.warnings:
        logger.warning("catalog %s: %s", path, issue)
    if report.warnings or report.errors:
        logger.warning(
            "📖 Каталог загружен с замечаниями: %s ошибок, %s предупреждений (подробнее: /catalog_check)",
            len(report.errors), len(report.warnings),
        )


def _apply_catalog(data: dict) -> dict:
    """Publishes a catalog dict as the process-wide snapshot."""
    global CATALOG_DATA, SERVICES_LIST, SERVICES_JSON
    CATALOG_DATA = data
    SERVICES_LIST = CATALOG_DATA.get("services_list", [])
    SERVICES_JSON = json.dumps(SERVICES_LIST, ensure_ascii=False)
    return CATALOG_DATA


CATALOG_DATA: dict = {}
SERVICES_LIST: list = []
SERVICES_JSON: str = "[]"
_apply_catalog(_load_catalog())
CEMETERIES_JSON = json.dumps(CEMETERIES_DICT, ensure_ascii=False)


def catalog_is_loaded() -> bool:
    """Whether catalog.json was found and parsed. Used as a startup health check."""
    return bool(CATALOG_DATA)


def check_catalog() -> CatalogReport:
    """Re-validates the in-memory catalog. Backs the /catalog_check command."""
    return validate_catalog(CATALOG_DATA)


def reload_catalog() -> dict:
    """Re-read catalog.json from disk (used by the Telegram admin panel after edits)."""
    return _apply_catalog(_load_catalog())


def load_catalog_from(path) -> dict:
    """Loads an arbitrary catalog file as the active catalog.

    Exists for offline tooling (`tools/simulate_order.py`, tests) that needs
    to run the real business rules against a specific catalog file without
    moving files around or mutating module globals by hand.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _log_catalog_report(validate_catalog(data), path)
    return _apply_catalog(sanitize_catalog(data))
