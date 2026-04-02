import time
import logging
import os
import mimetypes
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
import google.generativeai as genai
from PIL import Image, ImageOps
import io

from src.utils import clean_json_response, safe_parse_json, deduplicate_services
from src.config import VISION_MODEL_NAME

try:
    import fitz
except ImportError:
    fitz = None

logger = logging.getLogger(__name__)
VISION_MODEL = genai.GenerativeModel(VISION_MODEL_NAME)

MAX_IMAGE_SIZE = 20 * 1024 * 1024
TARGET_MAX_SIZE_KB = 10 * 1024

def fix_image_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except:
        return img

def optimize_image_bytes(img: Image.Image, filename: str = "image", max_size_kb: int = TARGET_MAX_SIZE_KB) -> bytes:
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
        return optimize_image_bytes(img, Path(image_path).name, max_size_kb)

def process_pdf(pdf_path: str) -> List[dict]:
    if not fitz:
        logger.error("❌ Библиотека PyMuPDF не установлена! PDF игнорируются.")
        return []
    parts = []
    pdf_document = fitz.open(pdf_path)
    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        optimized_data = optimize_image_bytes(img, f"{Path(pdf_path).name}_page_{page_num + 1}")
        parts.append({"mime_type": "image/jpeg", "data": optimized_data})
    return parts

def validate_extracted_data(data: Dict) -> bool:
    if 'deceased' not in data:
        logger.error("❌ Отсутствует обязательный блок deceased")
        return False
    return True

def prepare_input_files(file_paths: List[str]) -> List[dict]:
    image_parts = []
    for path in file_paths:
        if not os.path.exists(path):
            continue
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type == 'application/pdf':
            image_parts.extend(process_pdf(path))
        else:
            size = os.path.getsize(path)
            if size > MAX_IMAGE_SIZE:
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
    logger.info("👀 АГЕНТ 1: Оцифровка бланка (Скоростной режим)...")
    start_time = time.time()
    image_parts = prepare_input_files(file_paths)
    if not image_parts: return "{}"

    prompt = """
    Ты — AI-оцифровщик ритуальных бланков. Твоя цель — максимальная скорость и точность.
    Верни СТРОГО один JSON-объект без какого-либо текста, разметки или пояснений.

    СТРУКТУРА JSON:
    {
      "deceased": {"fio": "", "birth_date": "", "death_date": "", "burial_date": "", "cemetery": ""},
      "customer": {"fio": "", "phone": ""},
      "transport": [{"name": "", "price": 0, "quantity": 1}],
      "services": [{"name": "", "price": 0, "quantity": 1}],
      "goods": [{"name": "", "price": 0, "quantity": 1}],
      "handwritten_total": 0
    }

    ПРАВИЛА:
    1. Распредели услуги в `services`, автотранспорт в `transport`, а товары (труна, вінок, хрест, подушка и т.д.) в `goods`.
    2. `handwritten_total` — найди итоговую сумму в самом низу бланка (Сума / Разом) и запиши ТОЛЬКО число.
    3. Если количество не указано, пиши 1.
    """

    content = [prompt] + image_parts

    for attempt in range(retries):
        try:
            response = VISION_MODEL.generate_content(content, generation_config={"temperature": 0.0})
            data = safe_parse_json(response.text, expected_type='object')

            if not data or not isinstance(data, dict):
                raise ValueError("Ответ не словарь или пуст")

            for cat in ['services', 'transport', 'goods']:
                if cat in data:
                    data[cat] = deduplicate_services(data[cat])

            if not validate_extracted_data(data):
                raise ValueError("Невалидная структура данных")

            elapsed = time.time() - start_time
            logger.info(f"✅ АГЕНТ 1 завершил парсинг за {elapsed:.2f} сек.")
            return json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"⚠️ Ошибка Агента 1 (попытка {attempt + 1}): {e}")
            time.sleep(2 ** attempt)

    logger.critical("❌ Агент 1 провалил задачу.")
    return "{}"