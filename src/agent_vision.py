import time
import logging
import os
import mimetypes
import json
import io
from pathlib import Path
from typing import List, Dict, Union

from PIL import Image, ImageOps

from google import genai
from google.genai import types

from src.utils import safe_parse_json, deduplicate_services
from src.config import VISION_MODEL_NAME

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

logger = logging.getLogger(__name__)
client = genai.Client()

MAX_IMAGE_SIZE = 20 * 1024 * 1024
TARGET_MAX_SIZE_KB = 10 * 1024


def fix_image_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except Exception:  # Исправлено: уточнен тип исключения
        return img


def optimize_image_bytes(img: Image.Image, max_size_kb: int = TARGET_MAX_SIZE_KB) -> bytes:
    # Исправлено: убран неиспользуемый аргумент filename
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


def process_pdf(pdf_path: str) -> List[dict]:
    if fitz is None:
        logger.error("❌ Библиотека PyMuPDF не установлена! PDF игнорируются.")
        return []
    parts = []
    pdf_document = fitz.open(pdf_path)  # type: ignore
    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))  # type: ignore

        # Исправлено: Pillow ждет кортеж (), а не список []
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        optimized_data = optimize_image_bytes(img)
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
    logger.info("👀 АГЕНТ 1: Оцифровка бланка (Активирован Thinking-режим)...")
    start_time = time.time()

    image_parts = prepare_input_files(file_paths)
    if not image_parts:
        return "{}"

    prompt = """
        Ты — AI-оцифровщик ритуальных бланков. Твоя цель — извлечь данные СТРОГО как они написаны.

        🚨 ПРАВИЛО МАТЕМАТИКИ:
        В бланке часто указано количество человек (например, 4) и общая сумма (например, 6400).
        НИКОГДА не умножай цену на количество самостоятельно! 
        Если в колонке 'Сума' стоит число — это финальная стоимость всей услуги.
        Записывай её в 'price', а в 'quantity' всегда ставь 1 для услуг 'Закопування' и 'снос', чтобы избежать двойного умножения в будущем.

        СТРУКТУРА JSON:
        {
          "deceased": {"fio": "", "birth_date": "", "death_date": "", "burial_date": "", "cemetery": ""},
          "customer": {"fio": "", "phone": ""},
          "services": [{"name": "", "price": 0, "quantity": 1}],
          "transport": [{"name": "", "price": 0, "quantity": 1}],
          "goods": [{"name": "", "price": 0, "quantity": 1}],
          "handwritten_total": 0
        }

        ПРАВИЛА:
        1. Для всех услуг по умолчанию 'quantity' = 1, если это не товары (платки, свечи).
        2.ВАЖНО: Если в строке написано 'Послуги персоналу 4 (чел) --- 8000', ты пишешь quantity: 4, price: 8000. 
    Никакого деления или умножения на этом этапе! Цена может меняться и количество но логика остается та же.
        3. `handwritten_total` — это число из самого низа бланка.
        
        🚨 ПРАВИЛО РАЗДЕЛЕНИЯ (ВАЖНО):
    Если в колонке 'Сума' ты видишь запись вида '6400 + 1400' или '6400' с припиской '+1400' ниже:
    1. Первое число (6400) — это цена услуги 'Закопування'. Она идет в блок `services`.
    2. Второе число (1400) — это цена товара 'Рушник для опускання'. Она ОБЯЗАТЕЛЬНО идет в блок `goods`
    
    🚨 ЛОГИКА ДЛЯ СКЛАДА:
    Все физические предметы (Труна, Хрест, Вінок, Рушник, Хусточки) должны попадать СТРОГО в блок `goods`. 
    Даже если агент написал их в разделе услуг — переноси их в `goods`.
    """


    # Исправлено: Явно указываем IDE, что в списке могут быть и строки, и объекты Part
    contents: List[Union[str, types.Part]] = [prompt]

    for part in image_parts:
        contents.append(
            types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"])
        )

    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=VISION_MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    # Исправлено: Затыкаем IDE, SDK нормально парсит стрингу "high"
                    thinking_config=types.ThinkingConfig(thinking_level="high")  # type: ignore
                )
            )

            data = safe_parse_json(response.text, expected_type='object')

            if not data or not isinstance(data, dict):
                raise ValueError("Ответ не словарь или пуст")

            if 'services' in data:
                data['services'] = deduplicate_services(data['services'])

            if not validate_extracted_data(data):
                raise ValueError("Невалидная структура данных")

            elapsed = time.time() - start_time
            logger.info(f"🧠 АГЕНТ 1 подумал и выдал результат за {elapsed:.2f} сек.")

            return json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"⚠️ Ошибка парсинга (попытка {attempt + 1}): {e}")
            time.sleep(2 ** attempt)

    return "{}"