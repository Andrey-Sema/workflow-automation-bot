"""Low-level 1C tab-switching: finds a tab by matching a saved screenshot
template against the current screen and clicks its center.

The interaction itself (pyautogui image match + click) is deliberately left
alone — it's the one piece that's proven to reliably drive the real 1C
window over RDP. Everything here is about failing predictably and
recoverably around that interaction, not about replacing it.
"""

import logging
import time
from pathlib import Path

try:
    import pyautogui
except Exception:  # pragma: no cover - only fails outside the automation container
    # Deliberately broader than ImportError. With pyautogui installed but no
    # X display reachable, the failure comes from deep inside its own import
    # (mouseinfo does os.environ['DISPLAY'] at module scope) and surfaces as
    # KeyError, not ImportError — so an ImportError-only guard let it escape
    # and killed the process at import time, before any of the friendly
    # "pyautogui недоступен" handling below could run. That happens on a
    # headless machine and in the window between Xvfb starting and DISPLAY
    # being usable.
    pyautogui = None

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "data" / "templates"


def click_tab_by_image(
    image_filename: str,
    confidence: float = 0.8,
    attempts: int = 2,
    retry_delay: float = 0.5,
) -> bool:
    """Ищет вкладку на экране по картинке и кликает по ней.

    Retries a couple of times on a plain "not found" - over RDP the frame
    can be mid-repaint exactly when the screenshot is taken, and that's
    worth one more look rather than failing the whole наряд outright. A
    template file actually missing from disk is checked separately up
    front, since no amount of retrying fixes that.
    """
    if pyautogui is None:
        logger.error("❌ pyautogui недоступен в этом окружении — не могу искать вкладку на экране.")
        return False

    image_path = TEMPLATES_DIR / image_filename
    if not image_path.exists():
        logger.error(f"❌ Файл-шаблон не найден на диске: {image_path} — проверь data/templates/")
        return False

    attempts = max(attempts, 1)
    for attempt in range(1, attempts + 1):
        try:
            location = pyautogui.locateCenterOnScreen(str(image_path), confidence=confidence)
            if location:
                pyautogui.click(location)
                logger.info(f"✅ Успешно кликнул по вкладке: {image_filename}")
                time.sleep(0.3)
                return True
            logger.warning(f"⚠️ Не смог найти вкладку на экране: {image_filename} (попытка {attempt}/{attempts})")
        except pyautogui.ImageNotFoundException:
            logger.warning(f"⚠️ Картинка {image_filename} не найдена на экране (попытка {attempt}/{attempts})")
        except pyautogui.FailSafeException:
            # Deliberate human abort (cursor yanked to a screen corner) -
            # not a vision error, and retrying would defeat the point.
            logger.critical("🚨 Сработал pyautogui fail-safe (курсор в углу экрана) — прерываю автоматизацию.")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка компьютерного зрения: {e}")
            return False

        if attempt < attempts:
            time.sleep(retry_delay)

    return False
