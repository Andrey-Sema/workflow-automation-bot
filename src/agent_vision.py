import json
import time
import logging
import re
import os
import mimetypes
from pathlib import Path
from typing import List, Optional
import google.generativeai as genai
from PIL import Image
import io

logger = logging.getLogger(__name__)

# Стабильная модель для Vision задач
VISION_MODEL = genai.GenerativeModel('gemini-3-flash-preview')

# Ограничение только по весу файла, разрешение не трогаем!
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


def clean_json_response(text: str) -> str:
    """Извлекает JSON из текстового ответа."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text.strip()


def validate_image(image_path: str) -> Optional[str]:
    """Проверяет изображение и возвращает ошибку или None."""
    if not os.path.exists(image_path):
        return f"Файл не найден: {image_path}"

    size = os.path.getsize(image_path)
    if size > MAX_IMAGE_SIZE:
        return f"Файл слишком большой ({size / 1024 / 1024:.1f}MB): {image_path}"

    return None


def optimize_image(image_path: str) -> bytes:
    """Готовит изображение для API БЕЗ сжатия размеров, сохраняя 100% качество."""
    with Image.open(image_path) as img:
        # Конвертируем в RGB, если есть прозрачность
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # Сохраняем в память в максимальном качестве
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=100)
        return buffer.getvalue()


def get_mime_type(file_path: str) -> str:
    """Определяет MIME-тип файла."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type and mime_type.startswith('image/'):
        return mime_type
    return 'image/jpeg'


def extract_raw_data(image_paths: List[str], retries: int = 3) -> str:
    """Извлекает данные из изображений бланка заказа."""
    logger.info("👀 АГЕНТ 1: Оцифровка бланка...")

    # Валидация изображений
    for path in image_paths:
        error = validate_image(path)
        if error:
            logger.error(f"❌ {error}")
            return "{}"

    # Подготовка изображений
    image_parts = []
    for img_path in image_paths:
        try:
            optimized_data = optimize_image(img_path)
            image_parts.append({
                "mime_type": get_mime_type(img_path),
                "data": optimized_data
            })
            logger.debug(f"✅ Подготовлено: {Path(img_path).name} ({len(optimized_data) / 1024:.1f} KB)")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки {img_path}: {e}")
            return "{}"

    prompt = """
    Твоя задача — извлечь структурированные данные из фотографий рукописного бланка ритуального заказа.

    ВАЖНЫЕ БЛОКИ:
    1. ПОКОЙНЫЙ: ФИО, дата рождения (ОБЯЗАТЕЛЬНО, если есть), дата смерти, дата похорон, кладбище.
    2. ЗАКАЗЧИК: ФИО, телефон.
    3. ТРАНСПОРТ: все отмеченные пункты из блоков 'Транспортування' и 'Автотранспорт'. Указывай количество мест.
    4. УСЛУГИ: все пункты из 'Послуги для поховання' и 'Оренда'.
    5. ТОВАРЫ и АТРИБУТИКА (СМОТРИ ВНИМАТЕЛЬНО!):
       Обязательно ищи глазами слова: 'Труна', 'Хрест', 'Вінок' (венок), 'Стрічка' (лента), 'Хусточки' (платочки), 'Свічки' (свечи).

    🚨 ПРАВИЛО РАСПОЗНАВАНИЯ КОЛИЧЕСТВА (КРИТИЧЕСКИ ВАЖНО):
    В рукописных бланках количество часто пишут просто цифрой прямо в ячейке рядом с названием (например, 'Хусточки 5', 'Свічки 5', 'Вінок 2').
    Твоя задача — ОТДЕЛИТЬ название от цифры!
    Цифру запиши СТРОГО в поле 'quantity' (как число, а не строку), а само название (без цифры) в поле 'name'.
    Если есть название услуги/товара, но цифры рядом нет вообще — ставь quantity = 1.
    Будь предельно внимателен к рукописным пометкам на полях и внутри ячеек.
    НЕ считай итоговые суммы. Только извлеки сырые данные.

    Верни ТОЛЬКО JSON в формате:
    {
      "deceased": {"fio": "", "birth_date": "", "death_date": "", "burial_date": "", "cemetery": ""},
      "customer": {"fio": "", "phone": ""},
      "services": [{"name": "", "price": 0, "quantity": 1}]
    }
    """

    content = [prompt] + image_parts

    for attempt in range(retries):
        try:
            logger.info(f"📤 Попытка {attempt + 1}/{retries}...")

            response = VISION_MODEL.generate_content(
                content,
                generation_config={"temperature": 0.0}
            )

            raw_text = clean_json_response(response.text)

            if not raw_text or raw_text == "{}":
                raise ValueError("Пустой или невалидный JSON от модели")

            # Проверка структуры JSON
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                raise ValueError("Ответ не является словарем")

            logger.info(f"✅ Данные извлечены успешно ({len(raw_text)} символов)")
            return raw_text

        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Невалидный JSON: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка: {type(e).__name__}: {e}")

        if attempt < retries - 1:
            delay = 2 ** attempt
            logger.info(f"⏳ Повтор через {delay} сек...")
            time.sleep(delay)

    logger.error("❌ АГЕНТ 1 исчерпал все попытки.")
    return "{}"