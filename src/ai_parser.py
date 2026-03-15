import logging
from src.agent_vision import extract_raw_data
from src.agent_logic import validate_and_normalize

logger = logging.getLogger(__name__)


# Добавили booked_in_1c
def parse_images_with_gemini(image_paths: list, num_addresses: int, booked_in_1c: list) -> tuple[str, dict]:
    logger.info("🔄 Запускаю конвейер Nier:Automato...")

    raw_json_str = extract_raw_data(image_paths)
    if not raw_json_str or raw_json_str == "{}":
        logger.error("❌ AGENT 1 вернул пустые данные.")
        return "", {}

    # Передаем брони второму агенту
    final_data = validate_and_normalize(raw_json_str, num_addresses, booked_in_1c)

    if not final_data:
        logger.error("❌ AGENT 2 не смог применить бизнес-логику.")
        return raw_json_str, {}

    logger.info("✅ Конвейер успешно завершен.")
    return raw_json_str, final_data