import os
import json
import logging
import shutil
import time
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

# --- ИМПОРТЫ НАШИХ МОДУЛЕЙ ---
from src.agent_booked_ocr import get_booked_items_via_screenshot
from src.ai_parser import parse_images_with_gemini
from src.validator import validate_and_fix_order

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# Загрузка окружения
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY не найден в .env!")

# Настройка путей
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "data" / "input"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
OUTPUT_DIR = BASE_DIR / "data" / "output"

# Создаем папки, если их нет
for d in [INPUT_DIR, PROCESSED_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def get_photos_for_order(num_photos: int) -> list[Path]:
    """Собирает нужное количество фоток из папки input, начиная с самых старых."""
    all_files = [f for f in INPUT_DIR.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
    if not all_files:
        logger.warning("⚠️ Папка input пуста! Закинь фотки наряда.")
        return []

    all_files.sort(key=lambda x: x.stat().st_mtime)

    if len(all_files) < num_photos:
        logger.warning(f"⚠️ Нужно {num_photos} фоток, а в папке только {len(all_files)}!")
        return []

    return all_files[:num_photos]


def run_cli():
    logger.info("=" * 50)
    logger.info("🤖 Nier:Automato v2.0 | Enterprise Edition")
    logger.info("=" * 50)

    genai.configure(api_key=API_KEY)

    while True:
        start_cmd = input("\n👉 Введи '1' для старта (или 'q' для выхода): ").strip()
        if start_cmd.lower() == 'q':
            break
        if start_cmd != '1':
            continue

        # Запрос количества фото
        while True:
            try:
                num_photos = int(input("📸 Сколько фото в этом наряде?: "))
                if 1 <= num_photos <= 10:
                    break
                print("⚠️ Введи от 1 до 10!")
            except ValueError:
                print("⚠️ Бля, введи цифру!")

        # Запрос количества адресов (доп. точек)
        while True:
            try:
                num_addresses = int(input("📍 Сколько адресов (доп. точек) в маршруте? (обычно 0-3): "))
                if 0 <= num_addresses <= 10:
                    break
                print("⚠️ Введи реальное число адресов!")
            except ValueError:
                print("⚠️ Нужна цифра!")

        # --- СТЕЛС-МОДУЛЬ 1С ---
        scan_1c = input("🕵️‍♂️ Сканируем открытый наряд в 1С на дубликаты? (y/n): ").strip().lower()
        booked_in_1c = []
        if scan_1c == 'y':
            input("👉 Нажми Enter и переместись в экран наряда 1С для прчотения услуг")
            booked_in_1c = get_booked_items_via_screenshot()

        photos_to_process = get_photos_for_order(num_photos)
        if not photos_to_process:
            continue

        logger.info(
            f"🚀 В работе {len(photos_to_process)} файла(ов). Адресов: {num_addresses}. Броней в 1С: {len(booked_in_1c)}")

        # --- ТАЙМИНГ ПО БЛОКАМ ---
        start_time = time.time()

        # 1. Парсинг и Бизнес-логика (Vision + Text LLM + Python Engine)
        raw_json_str, final_json_data = parse_images_with_gemini(photos_to_process, num_addresses, booked_in_1c)

        end_time = time.time()
        # -------------------------

        if final_json_data:
            elapsed = round(end_time - start_time, 2)
            logger.info(f"⏱ Нейросети отработали за: {elapsed} сек.")

            # 2. 🔥 ТАМОЖНЯ PYDANTIC (Проверка структуры и математики)
            final_json_data = validate_and_fix_order(final_json_data)

            if not final_json_data:
                logger.error("❌ Данные не прошли Pydantic-валидацию. Разбирайся с логами.")
                continue

            # --- ВЫВОД АЛЕРТОВ ---
            dob = final_json_data.get("deceased", {}).get("birth_date", "")
            if dob == "01.01.1920":
                logger.warning(
                    "🚨 СЕНТИЛЬНЫЙ ВАРНИНГ: Дата рождения не указана на бланке. Уточни у агента для таблички!")

            warnings_list = final_json_data.get("warnings", [])
            for w in warnings_list:
                logger.warning(f"🤬 АЛЕРТ ПО ДЕНЬГАМ/ПРАВИЛАМ: {w}")

            has_errors = any("ОШИБКА ВАЛИДАЦИИ" in str(s.get("name", "")) for s in final_json_data.get("services", []))
            if has_errors:
                logger.error("🚨 В услугах есть косяки распознавания! Чекай лог.")

            # --- СОХРАНЕНИЕ ЧЕРНОГО ЯЩИКА ---
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            log_filename = f"order_log_{timestamp}.txt"

            with open(OUTPUT_DIR / log_filename, "w", encoding="utf-8") as f:
                f.write("=== ШАГ 1: СЫРОЙ JSON (От Vision) ===\n" + raw_json_str + "\n\n")
                f.write("=== ШАГ 2: ФИНАЛЬНЫЙ JSON (После Pydantic и Python Engine) ===\n")
                f.write(json.dumps(final_json_data, indent=4, ensure_ascii=False))

            logger.info(f"💾 Лог сохранен: {log_filename}")

            # --- ПЕРЕМЕЩЕНИЕ ФОТОК ---
            for p in photos_to_process:
                for attempt in range(3):
                    try:
                        shutil.move(str(p), str(PROCESSED_DIR / p.name))
                        break
                    except Exception as e:
                        time.sleep(1)  # Костыль под локи Windows
                        if attempt == 2:
                            logger.error(f"❌ Ошибка перемещения {p.name}: {e}")

            logger.info(f"✅ Наряд успешно отработан и готов к вводу.")
        else:
            logger.error("❌ Фатальная ошибка парсинга конвейера.")


if __name__ == "__main__":
    run_cli()