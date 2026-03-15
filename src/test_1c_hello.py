import keyboard
import time


def test_1c_injection():
    print("⚠️ ПОДГОТОВКА: Открой 1С.")
    print(
        "У тебя 5 секунд! Кликни мышкой в любую ячейку (например, в 'Номенклатуру' или 'Сумму'), чтобы там замигал курсор.")

    for i in range(3, 0, -1):
        print(f"Старт через {i}...")
        time.sleep(1)

    print("👻 Поехали! Печатаю 'Привет' и жму Enter...")

    # Печатаем слово с задержкой 0.05 сек между буквами
    keyboard.write("Доставка", delay=0.02)
    time.sleep(0.2)
    keyboard.send('enter')

    print("✅ Тест завершен. Проверяй 1С!")


if __name__ == "__main__":
    test_1c_injection()