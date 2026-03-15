import json
import time
import logging
import re
import google.generativeai as genai

logger = logging.getLogger(__name__)

# Используем полноценный Flash для OCR - он стабильнее видит мелкий текст
VISION_MODEL = genai.GenerativeModel('gemini-3-flash-preview')


def clean_json_response(text: str) -> str:
    """Умный поиск JSON через регулярку"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group()
    return text.strip()


def extract_raw_data(image_paths: list, retries: int = 3) -> str:
    logger.info("👀 АГЕНТ 1 (Vision 3.1 Flash): Оцифровка бланка...")

    # Передаем изображения как байты (Bytes), а не PIL объекты
    image_parts = []
    for img_path in image_paths:
        with open(img_path, "rb") as f:
            image_parts.append({
                "mime_type": "image/jpeg",  # или определи динамически через mimetypes
                "data": f.read()
            })

    prompt = """
    Extract structured data from the funeral order form images.
    Follow the layout anchors:
    1. DECEASED: FIO, birth_date (REQUIRED), death_date, burial_date, cemetery.
    2. CUSTOMER: FIO, phone.
    3. TRANSPORT: Extract all marked items (with price/ticks) from blocks 'Транспортування' and 'Автотранспорт'. 
    4. SERVICES: Extract all items from 'Послуги для поховання' and 'Оренда'.
    5. GOODS: Extract 'Труна', 'Хрест', 'Вінок', 'Атрибутика' with prices.

    IMPORTANT:
    - If "birth_date" is missing or unreadable, leave it empty.
    - If a service has a price but quantity is not specified, assume 1.
    - For transport items (№6, №7), include the number of seats (e.g., "18 місць").
    - DO NOT calculate any totals. Just extract raw data.

    Return ONLY JSON.
    """

    content = [prompt] + image_parts

    for attempt in range(retries):
        try:
            response = VISION_MODEL.generate_content(
                content,
                generation_config={"temperature": 0}
            )
            raw_text = clean_json_response(response.text)

            if not raw_text or raw_text == "{}":
                raise ValueError("Empty or invalid JSON from Gemini")

            # Проверяем на валидность JSON
            json.loads(raw_text)
            return raw_text

        except Exception as e:
            logger.warning(f"⚠️ АГЕНТ 1 споткнулся ({attempt + 1}/{retries}) [{type(e).__name__}]: {e}")
            time.sleep(2 ** attempt)

    logger.error("❌ АГЕНТ 1 сдался.")
    return "{}"