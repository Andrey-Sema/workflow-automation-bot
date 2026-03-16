import pyautogui
import io
import logging
import json
import time
import re
from typing import List, Dict, Any
from pathlib import Path
from datetime import datetime
import google.generativeai as genai

logger = logging.getLogger(__name__)

# 🔥 ЖЕСТКО ЗАШИТАЯ МОДЕЛЬ ДЛЯ АГЕНТА №3 🔥
BOOKED_MODEL = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
DEBUG_DIR = Path("debug_screenshots")


def clean_json_response(text: str) -> str:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group() if match else text.strip()


def safe_int(value: Any, default: int = 0) -> int:
    if value is None: return default
    if isinstance(value, (int, float)): return int(value)
    if isinstance(value, str):
        cleaned = re.sub(r'\s+', '', value.replace(',', '.'))
        cleaned = re.sub(r'[₴₽$€]|\b(грн|руб|usd|eur)\b', '', cleaned, flags=re.I)
        try:
            return int(float(cleaned))
        except ValueError:
            return default
    return default


def clean_service_name(name: Any) -> str:
    if not isinstance(name, str): name = str(name)
    name = re.sub(r'^[\s\d]*[-\*\.]\s*', '', name.strip())
    name = name.strip('"\'«»„“')
    name = re.sub(r'\s+', ' ', name)
    return re.sub(r'[-–—]\s*$', '', name).strip()


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
        debug_path = DEBUG_DIR / f"screenshot_{timestamp}.jpg"
        # Сохраняем оригинал в 100% качестве для логов
        full_screen_img.save(debug_path, format='JPEG', quality=100)

        # Готовим байты БЕЗ СЖАТИЯ размера
        img_byte_arr = io.BytesIO()
        full_screen_img.save(img_byte_arr, format='JPEG', quality=100)
        optimized_bytes = img_byte_arr.getvalue()

        logger.info(f"📤 Отправляю {len(optimized_bytes) / 1024:.1f} KB в Gemini...")

        prompt = """
        Ты — AI-сканер интерфейса 1С. На скриншоте открыта таблица "Услуги".
        Найди таблицу и извлеки данные строго из следующих колонок:
        1. "Номенклатура" -> запиши в ключ "name"
        2. "Количество" -> запиши в ключ "quantity"
        3. "Цена" -> запиши в ключ "price"
        4. "Сумма" -> запиши в ключ "sum"

        ВАЖНО:
        - Бери только реальные строки с услугами (игнорируй пустые строки и общие итоги внизу).
        - Верни СТРОГО JSON-массив.
        """
        response = BOOKED_MODEL.generate_content([prompt, {"mime_type": "image/jpeg", "data": optimized_bytes}])
        raw_text = clean_json_response(response.text)

        if not raw_text or raw_text == "[]":
            logger.warning("⚠️ ИИ не нашел услуг на скрине.")
            return []

        raw_items = json.loads(raw_text)
        items = validate_items(raw_items)
        logger.info(f"✅ Распознано {len(items)} услуг из 1С")
        return items

    except Exception as e:
        logger.error(f"❌ Ошибка визуального агента: {e}", exc_info=True)
        return []