"""Agent 3: reads whatever the 1C "Услуги" table currently shows on the
remote desktop, so the pipeline can drop items that are already booked and
avoid duplicate entries.

This module used to block on `input()` and ask a human to physically switch
screens. In the Docker/RDP setup the remote desktop is rendered continuously
onto a virtual display (Xvfb), so a screenshot can be taken at any time
without any interactive prompt — the function below is a pure
screenshot -> Gemini -> parsed-items call. The CLI entry point (`main.py`)
still wraps it with a countdown for local, non-RDP debugging.
"""

import io
import logging
import time
from datetime import datetime
from typing import Any

from google.genai import types

from src.circuit_breaker import gemini_circuit_breaker
from src.config import BOOKED_MODEL_NAME
from src.gemini_client import get_client
from src.metrics import GEMINI_CALL_DURATION, GEMINI_CALLS
from src.settings import get_settings
from src.utils import clean_service_name, safe_int, safe_parse_json

try:
    import pyautogui
except Exception:  # pragma: no cover - only fails outside the automation container
    # Same guard as src/win_1c_bot.py. This module is imported by the
    # Telegram handlers, so an unguarded import meant a headless start took
    # the whole bot down at import time — including the parts that never
    # touch the screen. With pyautogui=None the screenshot call below raises
    # and is handled like any other capture failure: log and skip the scan.
    pyautogui = None

logger = logging.getLogger(__name__)

# Debug screenshots are pure diagnostics; without a cap the directory grows
# by one JPEG per duplicate-scan forever and slowly eats the data volume.
MAX_DEBUG_SCREENSHOTS = 50

BOOKED_ITEMS_PROMPT = """
Ты — AI-сканер интерфейса 1С. На скриншоте открыта таблица "Услуги".
Найди таблицу и извлеки данные строго из колонок: "Номенклатура", "Количество", "Цена", "Сумма".
Верни СТРОГО JSON-массив [...]. Никакого текста до или после.
"""


def validate_items(items: list[dict]) -> list[dict]:
    valid = []
    seen_names = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = clean_service_name(item.get("name", ""))
        if len(name) < 3 or not any(c.isalpha() for c in name) or name in seen_names:
            continue

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


def _prune_old_screenshots(debug_dir) -> None:
    try:
        shots = sorted(debug_dir.glob("screenshot_*.jpg"))
        for stale in shots[:-MAX_DEBUG_SCREENSHOTS]:
            stale.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"⚠️ Не удалось почистить старые debug-скриншоты: {e}")


def capture_booked_items_screenshot() -> list[dict[str, Any]]:
    """Screenshots the current display (Xvfb/RDP or local), asks Gemini to
    read the 1C "Услуги" table off it, and returns the parsed+validated
    items. No user interaction — safe to call from the Telegram bot.
    """
    logger.info("👁️ АГЕНТ 3 (визуальный контроль дублей) активирован...")

    try:
        full_screen_img = pyautogui.screenshot()
    except Exception as e:
        logger.error(f"❌ Не удалось сделать скриншот экрана: {e}")
        return []

    settings = get_settings()
    debug_dir = settings.data_dir / "debug_screenshots"
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_screen_img.save(debug_dir / f"screenshot_{timestamp}.jpg", format='JPEG', quality=85)
    _prune_old_screenshots(debug_dir)

    img_byte_arr = io.BytesIO()
    full_screen_img.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
    optimized_bytes = img_byte_arr.getvalue()

    logger.info(f"📤 Отправляю {len(optimized_bytes) / 1024:.1f} KB в Gemini...")

    try:
        client = get_client()
        call_start = time.time()
        response = client.models.generate_content(
            model=BOOKED_MODEL_NAME,
            contents=[
                BOOKED_ITEMS_PROMPT,
                types.Part.from_bytes(data=optimized_bytes, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        GEMINI_CALL_DURATION.labels(agent="booked_ocr").observe(time.time() - call_start)

        raw_items = safe_parse_json(response.text, expected_type='array')
        if not raw_items:
            GEMINI_CALLS.labels(agent="booked_ocr", outcome="error").inc()
            gemini_circuit_breaker.record_failure()
            logger.warning("⚠️ ИИ не нашел услуг на скрине или вернул кривой ответ.")
            return []

        items = validate_items(raw_items)
        logger.info(f"✅ Распознано {len(items)} услуг из 1С")
        GEMINI_CALLS.labels(agent="booked_ocr", outcome="success").inc()
        gemini_circuit_breaker.record_success()
        return items

    except Exception as e:
        GEMINI_CALLS.labels(agent="booked_ocr", outcome="error").inc()
        gemini_circuit_breaker.record_failure()
        logger.error(f"❌ Ошибка визуального агента: {e}", exc_info=True)
        return []


def get_booked_items_via_screenshot_cli() -> list[dict[str, Any]]:
    """Interactive CLI wrapper: gives a human operator time to switch to the
    1C window before the screenshot is taken. Only used by the local CLI
    (`main.py`) — the Telegram/Docker flow calls
    `capture_booked_items_screenshot()` directly since the RDP desktop is
    already showing 1C continuously.
    """
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

    return capture_booked_items_screenshot()
