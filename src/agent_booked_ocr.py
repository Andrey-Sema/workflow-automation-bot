import pyautogui
import io
import logging
import time
from typing import List, Dict, Any
from pathlib import Path
from datetime import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv
import os

from src.config import BOOKED_MODEL_NAME
from src.utils import safe_int, clean_service_name, safe_parse_json

load_dotenv()

logger = logging.getLogger(__name__)

# Явно передаем ключ из переменной GEMINI_API_KEY
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
DEBUG_DIR = Path("debug_screenshots")


def validate_items(items: List[Dict]) -> List[Dict]:
    valid = []
    seen_names = set()
    for item in items:
        if not isinstance(item, dict): continue
        name = clean_service_name(item.get("name", ""))
        if len(name) < 3 or not any(c.isalpha() for c in name) or name in seen_names: continue

        qty = safe_int(item.get("quantity", 1), default=1)
        price = safe_int(item.get("price", 0), default=0)
        total = safe_int(item.get("sum", 0), default=0)

        if qty > 0 and price > 0:
            expected_sum = qty * price
            if expected_sum != total and total > 0:
                total = expected_sum

        valid.append({
            "name": name,
            "quantity": max(1, qty),
            "price": max(0, price),
            "sum": max(0, total)
        })
        seen_names.add(name)
    return valid


def get_booked_items_via_screenshot() -> List[Dict[str, Any]]:
    logger.info("👁️ Агент №3 (Визуальный Ниндзя) активирован...")
    print("\n" + "=" * 70)
    print("⚠️ ВИЗУАЛЬНОЕ СКАНИРОВАНИЕ 1С ⚠️")
    print("1. Нажми Enter в этой консоли.")
    print("2. Быстро разверни окно удаленки с 1С на весь экран.")
    print("3. Убедись, что таблицу с бронями ХОРОШО ВИДНО.")
    print("=" * 70)
    input("👉 Нажми Enter и выведи табличку 1С на экран... ")

    for i in range(3, 0, -1):
        logger.info(f"⏳ {i}...")
        time.sleep(1)
    logger.info("📸 ФОТОГРАФИРУЮ ЭКРАН!")

    try:
        full_screen_img = pyautogui.screenshot()
        DEBUG_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        full_screen_img.save(DEBUG_DIR / f"screenshot_{timestamp}.jpg", format='JPEG', quality=85)

        img_byte_arr = io.BytesIO()
        full_screen_img.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
        optimized_bytes = img_byte_arr.getvalue()

        logger.info(f"📤 Отправляю {len(optimized_bytes) / 1024:.1f} KB в Gemini...")

        prompt = """
        Ты — AI-сканер интерфейса 1С. На скриншоте открыта таблица "Услуги".
        Найди таблицу и извлеки данные строго из колонок: "Номенклатура", "Количество", "Цена", "Сумма".
        Верни СТРОГО JSON-массив [...]. Никакого текста до или после.
        """

        # Полностью новый синтаксис запроса Google AI
        response = client.models.generate_content(
            model=BOOKED_MODEL_NAME,
            contents=[
                prompt,
                types.Part.from_bytes(data=optimized_bytes, mime_type="image/jpeg")
            ],
            config=types.GenerateContentConfig(temperature=0.0)
        )

        raw_items = safe_parse_json(response.text, expected_type='array')

        if not raw_items or not isinstance(raw_items, list):
            logger.warning("⚠️ ИИ не нашел услуг на скрине или вернул кривой ответ.")
            return []

        items = validate_items(raw_items)
        logger.info(f"✅ Распознано {len(items)} услуг из 1С")
        return items

    except Exception as e:
        logger.error(f"❌ Ошибка визуального агента: {e}", exc_info=True)
        return []