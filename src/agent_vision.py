import io
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

from google.genai import types
from PIL import Image, ImageOps

from src.config import VISION_MODEL_NAME
from src.gemini_client import get_client
from src.utils import deduplicate_services, safe_parse_json

try:
    import fitz
    HAS_FITZ = True
except ImportError:
    fitz = None
    HAS_FITZ = False

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 20 * 1024 * 1024
TARGET_MAX_SIZE_KB = 10 * 1024
# Защита от decompression-bomb атак: отклоняем файлы с абсурдным разрешением
# ещё до полной декомпрессии в память (Pillow сам умеет это делать через
# Image.MAX_IMAGE_PIXELS, но мы дублируем проверку размера файла заранее).
MAX_PIXELS = 60_000_000

VISION_PROMPT = """
Ты — AI-оцифровщик ритуальных бланков. Твоя цель — извлечь данные СТРОГО как они написаны.

🚨 ПРАВИЛО МАТЕМАТИКИ:
Используй колонку 'Сума' как итоговую цену "price". Не умножай и не дели ничего сам!

🚨 СКЛАДСКОЙ УЧЕТ:
Все физические предметы (Труна, Хрест, Вінок, Рушник, Хусточки, Свічки) ОБЯЗАТЕЛЬНО переноси в блок
"goods", даже если они вписаны в услуги.

СТРУКТУРА JSON:
{
  "deceased": {"fio": "", "birth_date": "", "death_date": "", "burial_date": "", "cemetery": ""},
  "customer": {"fio": "", "phone": ""},
  "services": [{"name": "", "price": 0, "quantity": 1}],
  "transport": [{"name": "", "price": 0, "quantity": 1}],
  "goods": [{"name": "", "price": 0, "quantity": 1}],
  "handwritten_total": 0
}
"""


def fix_image_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except (AttributeError, KeyError, IndexError):
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
        if img.width * img.height > MAX_PIXELS:
            raise ValueError(f"Изображение слишком большое ({img.width}x{img.height}), похоже на decompression-bomb")
        img = fix_image_orientation(img)
        return optimize_image_bytes(img, max_size_kb)


def process_pdf(pdf_path: str) -> list[dict[str, Any]]:
    if not HAS_FITZ or fitz is None:
        logger.error("❌ Библиотека PyMuPDF не установлена! PDF игнорируются.")
        return []

    parts = []
    pdf_document = fitz.open(pdf_path)
    try:
        for page_num in range(len(pdf_document)):
            page = pdf_document[page_num]
            matrix = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=matrix)

            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            optimized_data = optimize_image_bytes(img)
            parts.append({"mime_type": "image/jpeg", "data": optimized_data})
    finally:
        pdf_document.close()
    return parts


def validate_extracted_data(data: dict[str, Any]) -> bool:
    return 'deceased' in data


def prepare_input_files(file_paths: list[str]) -> list[dict[str, Any]]:
    image_parts = []
    for path in file_paths:
        if not Path(path).exists():
            continue
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type == 'application/pdf':
            image_parts.extend(process_pdf(path))
        else:
            if Path(path).stat().st_size > MAX_IMAGE_SIZE:
                logger.warning(f"⚠️ Пропущен файл {path}: превышен лимит {MAX_IMAGE_SIZE / 1024 / 1024:.0f} МБ")
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


def extract_raw_data(file_paths: list[str], retries: int = 3) -> str:
    logger.info("👀 АГЕНТ 1: Оцифровка бланка (Thinking Mode: HIGH)...")
    start_time = time.time()

    image_parts = prepare_input_files(file_paths)
    if not image_parts:
        return "{}"

    contents: list[str | types.Part] = [VISION_PROMPT]
    for part in image_parts:
        contents.append(types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"]))

    client = get_client()
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=VISION_MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
                ),
            )

            data = safe_parse_json(response.text, expected_type='object')
            if not data:
                raise ValueError("Ответ пуст или не является словарем")

            if 'services' in data:
                data['services'] = deduplicate_services(data['services'])

            if not validate_extracted_data(data):
                raise ValueError("Невалидная структура данных (нет блока deceased)")

            elapsed = time.time() - start_time
            logger.info(f"🧠 АГЕНТ 1 отработал за {elapsed:.2f} сек.")
            return json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt + 1} провалена: {e}")
            time.sleep(2 ** attempt)

    return "{}"
