import pyautogui
import time
from pathlib import Path
from src.win_1c_bot import click_tab_by_image, TEMPLATES_DIR


def test_1c_vision():
    print("🔍 Начинаю проверку зрения...")
    print(f"📁 Ищу шаблоны в папке: {TEMPLATES_DIR}")

    # Проверяем, существуют ли файлы физически
    files = ["tab_uslugi.png", "tab_sklad.png", "tab_prochie.png"]
    for f in files:
        path = TEMPLATES_DIR / f
        if path.exists():
            print(f"✅ Файл найден: {f}")
        else:
            print(f"❌ ФАЙЛ НЕ НАЙДЕН: {f}. Проверь имя и папку!")

    print("\n🖥️ У тебя есть 5 секунд, чтобы открыть окно 1С на нужной вкладке...")
    for i in range(5, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    print("\n🚀 Пробую найти вкладку 'Услуги'...")
    # confidence=0.7 даем чуть больше прав на ошибку при первом тесте
    if click_tab_by_image("tab_uslugi.png", confidence=0.7):
        print("🎯 ПОПАДАНИЕ! Мышь должна была кликнуть по Услугам.")
    else:
        print("💨 ПРОМАХ. Бот не увидел вкладку. Возможно, масштаб экрана не 100% или картинка обрезана иначе.")

    print("\n🚀 Пробую найти вкладку 'Склад'...")
    if click_tab_by_image("tab_sklad.png", confidence=0.7):
        print("🎯 ПОПАДАНИЕ! Мышь кликнула по Складу.")
    else:
        print("💨 ПРОМАХ по Складу.")


if __name__ == "__main__":
    test_1c_vision()