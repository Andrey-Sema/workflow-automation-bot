import time  # ВОТ ОНО, СПАСЕНИЕ ОТ NameError!
import pyautogui
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "data" / "templates"

def click_tab_by_image(image_filename: str, confidence: float = 0.8) -> bool:
    """Ищет вкладку на экране по картинке и кликает по ней."""
    image_path = str(TEMPLATES_DIR / image_filename)

    try:
        location = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)

        if location:
            pyautogui.click(location)
            logger.info(f"✅ Успешно кликнул по вкладке: {image_filename}")
            time.sleep(0.3)
            return True
        else:
            logger.warning(f"⚠️ Не смог найти вкладку на экране: {image_filename}")
            return False

    except pyautogui.ImageNotFoundException:
        logger.warning(f"⚠️ Картинка {image_filename} вообще не найдена на экране.")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка компьютерного зрения: {e}")
        return False