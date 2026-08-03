"""Local CLI entry point — mainly for debugging the pipeline without
Telegram. The primary, supported way to run orders is the Telegram bot
(`python -m src.telegram_bot`); see README.md.
"""

import logging
import shutil
import time
import uuid
from pathlib import Path

from src.agent_booked_ocr import get_booked_items_via_screenshot_cli
from src.errors import WorkflowError
from src.logging_setup import setup_logging
from src.onec_order_entry import OneCOrderEntryBot
from src.pipeline import run_pipeline, write_audit_log
from src.settings import get_settings
from src.summary_formatting import format_order_summary

settings = get_settings()
settings.ensure_directories()
setup_logging(settings.log_dir, settings.log_level, json_output=settings.log_json)
logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}


def get_all_photos_for_order() -> list[Path]:
    """Retrieves and sorts all image files from the input directory."""
    all_files = [f for f in settings.input_dir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]

    if not all_files:
        logger.warning("⚠️ Input directory is empty! Please drop the order photos into 'data/input'.")
        return []

    all_files.sort(key=lambda x: x.stat().st_mtime)
    return all_files


def run_cli() -> None:
    logger.info("=" * 50)
    logger.info("🤖 Workflow Automation Bot | CLI (debug mode)")
    logger.info("=" * 50)

    while True:
        start_cmd = input("\n👉 Введи '1' для старта (или 'q' для выхода): ").strip()
        if start_cmd.lower() == 'q':
            break
        if start_cmd != '1':
            continue

        photos_to_process = get_all_photos_for_order()
        if not photos_to_process:
            continue

        logger.info(f"📸 Найдено фото для обработки: {len(photos_to_process)} шт.")

        while True:
            try:
                num_addresses = int(input("📍 Сколько адресов (доп. точек) в маршруте? (обычно 0-3): "))
                if 0 <= num_addresses <= 10:
                    break
                print("⚠️ Введи реальное число адресов!")
            except ValueError:
                print("⚠️ Нужна цифра!")

        scan_1c = input("🕵️‍♂️ Сканируем открытый наряд в 1С на дубликаты? (1 - да / 2 - нет): ").strip()
        booked_in_1c = get_booked_items_via_screenshot_cli() if scan_1c == '1' else []

        logger.info(
            f"🚀 В работе {len(photos_to_process)} файла(ов). "
            f"Адресов: {num_addresses}. Броней в 1С: {len(booked_in_1c)}"
        )

        order_id = uuid.uuid4().hex[:8]
        try:
            result = run_pipeline(photos_to_process, num_addresses, booked_in_1c)
        except WorkflowError as e:
            logger.error(e.user_message)
            continue

        write_audit_log(result, order_id)
        print("\n" + format_order_summary(result.summary, order_id=order_id).replace("<b>", "").replace("</b>", ""))

        if result.summary.has_conflicts:
            logger.warning("⚠️ В наряде есть конфликты — см. сводку выше.")

        print("\n" + "!" * 40)
        confirm = input("🤔 ВБИВАЕМ В 1С? (1 - ДА, любая другая клавиша - отмена): ").strip()
        print("!" * 40 + "\n")

        if confirm != '1':
            logger.warning("🚫 Наряд отменен пользователем. Файлы остались в папке input.")
            continue

        bot = OneCOrderEntryBot(settings=settings)
        logger.info(f"⚡️ Ввод в 1С (dry_run={bot.dry_run})...")
        try:
            entered = bot.enter_order(result.order_data)
            logger.info(f"✅ Внесено позиций: {len(entered)}")
        except WorkflowError as e:
            logger.error(f"{e.user_message} Наряд НЕ перемещён в processed — проверь 1С вручную.")
            continue

        for img_path in photos_to_process:
            for attempt in range(3):
                try:
                    shutil.move(str(img_path), str(settings.processed_dir / img_path.name))
                    break
                except OSError:
                    time.sleep(1)
                    if attempt == 2:
                        logger.error(f"❌ Ошибка перемещения {img_path.name}")

        logger.info("✅ Наряд успешно отработан.")


if __name__ == "__main__":
    run_cli()
