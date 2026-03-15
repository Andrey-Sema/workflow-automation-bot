import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def validate_and_fix_order(order_data: dict) -> dict:
    """Проверяет математику и чинит пустые поля перед отправкой в 1С."""
    if not order_data:
        return {}

    logger.info("🔍 Начинаю валидацию данных заказа...")

    # 1. Фикс даты смерти
    deceased = order_data.get("deceased", {})
    if not deceased.get("death_date"):
        today_str = datetime.now().strftime("%d.%m.%Y")
        deceased["death_date"] = today_str
        logger.warning(f"⚠️ Дата смерти не указана агентом! Подставил сегодняшнюю: {today_str}")

    # 2. Математический контроль
    real_total = 0

    for svc in order_data.get("services", []):
        real_total += svc.get("price", 0)

    for wreath in order_data.get("wreaths", []):
        real_total += wreath.get("wreath_price", 0)
        real_total += wreath.get("ribbon_price", 0)

    agent_total = order_data.get("calculated_total", 0)

    if real_total != agent_total:
        logger.error(f"❌ АХТУНГ! Ошибка в расчетах агента!")
        logger.error(f"Сумма всех строк: {real_total} грн.")
        logger.error(f"Написано в 'Итого': {agent_total} грн.")
        logger.error("Разбирайся руками, прежде чем вбивать в 1С!")
    else:
        logger.info(f"✅ Математика сошлась копейка в копейку: {real_total} грн.")

    return order_data