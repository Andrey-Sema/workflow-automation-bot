# src/stealth_1c.py
import time
import logging
import pyautogui
import pyperclip

logger = logging.getLogger(__name__)


def get_booked_items_from_1c() -> list:
    """
    Читает открытое окно "Вывести список" в 1С через эмуляцию клавиатуры.
    Возвращает список забронированных услуг.
    """
    logger.info("⏳ У тебя 3 секунды! Переключись (Alt+Tab) на окно 'Вывести список' в 1С...")
    time.sleep(3)  # Ждем, пока ты переключишься

    # Имитируем нажатия клавиатуры (выделяем всё и копируем)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.2)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.2)

    # Закрываем окошко со списком (Ctrl+F4 закрывает внутренние окна в 1С)
    pyautogui.hotkey('ctrl', 'f4')

    raw_text = pyperclip.paste()
    booked_items = []

    if not raw_text:
        logger.warning("⚠️ Буфер обмена пуст!")
        return booked_items

    lines = raw_text.split('\n')
    if len(lines) < 2:
        return booked_items

    # Ищем индекс колонки "Номенклатура"
    header = lines[0].split('\t')
    try:
        name_idx = header.index("Номенклатура")
    except ValueError:
        logger.error("❌ Не нашел колонку 'Номенклатура'. Ты точно открыл таблицу услуг?")
        return booked_items

    # Достаем названия услуг
    for line in lines[1:]:
        cols = line.split('\t')
        if len(cols) > name_idx:
            item_name = cols[name_idx].strip()
            if item_name:
                booked_items.append(item_name)

    logger.info(f"🕵️‍♂️ Спиздил из 1С {len(booked_items)} позиций: {booked_items}")
    return booked_items