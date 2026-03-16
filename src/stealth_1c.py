import time
import logging
import re
import pyautogui
import pyperclip
import ctypes
from typing import List, Optional

logger = logging.getLogger(__name__)

# --- Системные константы ---
LANG_ENG = 0x0409
LANG_RUS = 0x0419
WM_INPUTLANGCHANGEREQUEST = 0x0050
# Флаги: активировать и установить для всего процесса
KLF_FLAGS = 0x00000001 | 0x00000004


def set_layout(lang_id: int, retries: int = 3) -> bool:
    """Устанавливает раскладку клавиатуры с проверкой через WinAPI."""
    for attempt in range(retries):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                time.sleep(0.1)
                continue

            # Загружаем нужную раскладку
            layout = ctypes.windll.user32.LoadKeyboardLayoutW(f"{lang_id:08X}", KLF_FLAGS)

            # Посылаем сигнал окну сменить раскладку
            ctypes.windll.user32.SendMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, layout)

            time.sleep(0.15)  # Пауза на применение

            # Проверяем, что реально стоит нужная раскладка (сравниваем младшее слово ID)
            current_layout = ctypes.windll.user32.GetKeyboardLayout(0)
            if (current_layout & 0xFFFF) == (lang_id & 0xFFFF):
                return True
        except Exception as e:
            logger.debug(f"Попытка смены языка {attempt + 1} не удалась: {e}")
            time.sleep(0.1)

    return False


def wait_for_window(title_part: str, timeout: int = 5) -> bool:
    """Ожидает появления окна."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            if title_part.lower() in buff.value.lower():
                return True
        time.sleep(0.3)
    return False


def safe_copy_to_clipboard(max_wait: float = 2.0) -> Optional[str]:
    """Безопасное копирование данных из 1С."""
    try:
        old_content = pyperclip.paste()

        # Выделяем всё и копируем
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'c')

        # Цикл ожидания обновления буфера
        start_time = time.time()
        while time.time() - start_time < max_wait:
            new_content = pyperclip.paste()
            # Если в буфере что-то новое или хотя бы не пусто (если старый был пуст)
            if new_content and new_content != old_content:
                return new_content
            time.sleep(0.1)

        # Вторая попытка копирования, если первая не прошла
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.3)
        return pyperclip.paste()
    except Exception as e:
        logger.error(f"❌ Ошибка буфера: {e}")
        return None


def parse_1c_table(text: str) -> List[str]:
    """Парсит текст из 1С, учитывая разные разделители и колонки."""
    if not text or not text.strip(): return []

    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    if len(lines) < 2: return []

    # Определяем разделитель
    first_line = lines[0]
    if '\t' in first_line:
        header = first_line.split('\t')
        separator = '\t'
    else:
        # Если 1С выдала пробелы вместо табов
        header = re.split(r'\s{2,}', first_line)
        separator = r'\s{2,}'

    # Ищем индекс колонки (расширенный поиск)
    name_idx = -1
    target_cols = ["номенклатура", "наименование", "товар", "послуга", "услуга", "изделие"]

    for i, col in enumerate(header):
        col_clean = col.strip().lower()
        if any(target in col_clean for target in target_cols):
            name_idx = i
            break

    if name_idx == -1:
        logger.error(f"❌ Колонку не нашли. Заголовки: {header[:4]}")
        return []

    items = []
    for line in lines[1:]:
        cols = line.split('\t') if separator == '\t' else re.split(separator, line)
        if len(cols) > name_idx:
            val = cols[name_idx].strip().strip('"').strip("'")
            # Нормализуем пробелы и проверяем длину
            val = ' '.join(val.split())
            if len(val) > 2:
                items.append(val)

    # Уникальные значения с сохранением порядка
    return list(dict.fromkeys(items))


def get_booked_items_from_1c(timeout: int = 10) -> List[str]:
    """Главная функция для 1С стелс-интеграции."""
    logger.info("🔍 Ниндзя готовится к прыжку в 1С...")

    if not wait_for_window("Вывести список", timeout):
        logger.warning("⚠️ Окно 1С не найдено. Нажми Enter в консоли, когда подготовишь окно...")
        input("Нажми Enter для продолжения...")

    try:
        # Устанавливаем ENG раскладку для команд
        if not set_layout(LANG_ENG):
            logger.error("❌ Не удалось переключить раскладку! Ctrl+C может не сработать.")
            # Продолжаем на удачу

        time.sleep(0.3)
        raw_text = safe_copy_to_clipboard()

        # Закрываем временное окно 1С (Alt+F4 или Ctrl+F4)
        pyautogui.hotkey('ctrl', 'f4')

        if not raw_text:
            logger.error("❌ Данные из 1С не получены.")
            return []

        result = parse_1c_table(raw_text)
        logger.info(f"✅ Из 1С успешно вытянуто уникальных позиций: {len(result)}")
        return result

    except Exception as e:
        logger.error(f"❌ Фатальный сбой стелс-модуля: {e}")
        return []
    finally:
        # Железно возвращаем раскладку на базу
        set_layout(LANG_RUS)