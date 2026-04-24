import time
import logging
import os
import mimetypes
import json
import io
# Удалили неиспользуемый импорт Path (Проблема №1)
from typing import List, Dict, Union, Any

from PIL import Image, ImageOps
from google import genai
from google.genai import types

from src.utils import safe_parse_json, deduplicate_services
from src.config import VISION_MODEL_NAME

# Исправляем Проблемы №2 и №3 (ссылки на None)
try:
    import fitz

    HAS_FITZ = True
except ImportError:
    fitz = None
    HAS_FITZ = False

logger = logging.getLogger(__name__)
client = genai.Client()

MAX_IMAGE_SIZE = 20 * 1024 * 1024
TARGET_MAX_SIZE_KB = 10 * 1024


def fix_image_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except (AttributeError, KeyError, IndexError):  # Уточнили Exception (Проблема №5)
        return img


def optimize_image_bytes(img: Image.Image, max_size_kb: int = TARGET_MAX_SIZE_KB) -> bytes:
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    buffer = io.BytesIO()
    quality = 95
    while quality > 70:
        buffer.seek(0)
        buffer.truncate(0)
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        if buffer.tell() / 1024 <= max_size_kb:
            break
        quality -= 5
    return buffer.getvalue()


def optimize_image(image_path: str, max_size_kb: int = TARGET_MAX_SIZE_KB) -> bytes:
    with Image.open(image_path) as img:
        img = fix_image_orientation(img)
        return optimize_image_bytes(img, max_size_kb)


def process_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    # Используем флаг HAS_FITZ, чтобы IDE не ругалась на None (Проблемы №2, №3)
    if not HAS_FITZ or fitz is None:
        logger.error("❌ Библиотека PyMuPDF не установлена! PDF игнорируются.")
        return []

    parts = []
    # Теперь IDE видит, что мы заходим сюда только если fitz НЕ None
    pdf_document = fitz.open(pdf_path)
    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        # Используем метод через точку, так безопаснее для анализатора
        matrix = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=matrix)

        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        optimized_data = optimize_image_bytes(img)
        parts.append({"mime_type": "image/jpeg", "data": optimized_data})

    pdf_document.close()
    return parts


def validate_extracted_data(data: Dict[str, Any]) -> bool:
    return 'deceased' in data


def prepare_input_files(file_paths: List[str]) -> List[Dict[str, Any]]:
    image_parts = []
    for path in file_paths:
        if not os.path.exists(path):
            continue
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type == 'application/pdf':
            image_parts.extend(process_pdf(path))
        else:
            if os.path.getsize(path) > MAX_IMAGE_SIZE:
                continue
            try:
                optimized_data = optimize_image(path)
                image_parts.append({
                    "mime_type": mime_type if mime_type and mime_type.startswith('image/') else 'image/jpeg',
                    "data": optimized_data
                })
            except Exception as e:
                logger.error(f"❌ Ошибка обработки {path}: {e}")
    return image_parts


def extract_raw_data(file_paths: List[str], retries: int = 3) -> str:
    logger.info("👀 АГЕНТ 1: Оцифровка бланка...")
    start_time = time.time()

    image_parts = prepare_input_files(file_paths)
    if not image_parts:
        return "{}"

    prompt = """
    Ты — AI-оцифровщик ритуальных бланков. Твоя цель — извлечь данные СТРОГО как они написаны.
    🚨 ПРАВИЛО МАТЕМАТИКИ: Используй колонку 'Сума' как итоговую цену "price".
    """

    contents: List[Union[str, types.Part]] = [prompt]
    for part in image_parts:
        contents.append(types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"]))

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=VISION_MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH)
                )
            )

            data = safe_parse_json(response.text, expected_type='object')
            if not data or not isinstance(data, dict):
                raise ValueError("Ответ пуст или не является словарем")

            if 'services' in data:
                data['services'] = deduplicate_services(data['services'])

            if not validate_extracted_data(data):
                raise ValueError("Невалидная структура данных")

            elapsed = time.time() - start_time
            logger.info(f"🧠 АГЕНТ 1 отработал за {elapsed:.2f} сек.")
            return json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt + 1} провалена: {e}")
            time.sleep(2 ** attempt)

    return "{}"