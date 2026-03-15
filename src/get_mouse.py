import pyautogui
import time

print("У тебя 5 секунд! Наведи мышку точно в центр вкладки 'Склад' в 1С и не двигай...")
time.sleep(5)
x, y = pyautogui.position()
print(f"Координаты вкладки 'Склад': X={x}, Y={y}")

print("\nТеперь у тебя 5 секунд! Наведи мышку в центр вкладки 'Услуги'...")
time.sleep(5)
x, y = pyautogui.position()
print(f"Координаты вкладки 'Услуги': X={x}, Y={y}")