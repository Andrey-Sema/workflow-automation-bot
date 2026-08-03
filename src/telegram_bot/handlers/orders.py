"""The main order flow: collect photos -> ask extra-address count -> ask
whether to scan 1C for duplicates -> run the Gemini pipeline -> show the
summary (FIO, handwritten vs. calculated total, conflicting lines) with
✅ Ввести в 1С / ❌ Отмена / 📄 Подробный лог buttons.

Every blocking call (Gemini, screenshot capture, pyautogui typing) runs via
`asyncio.to_thread` so one slow order never freezes the bot for everyone
else on the same event loop.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from src.agent_booked_ocr import capture_booked_items_screenshot
from src.errors import WorkflowError
from src.onec_order_entry import OneCOrderEntryBot
from src.order_store import CANCELLED, ENTERED, FAILED, PENDING, OrderStore
from src.pipeline import run_pipeline, write_audit_log
from src.settings import Settings
from src.summary_formatting import format_order_summary
from src.telegram_bot import keyboards as kb
from src.telegram_bot.file_intake import download_order_photo
from src.telegram_bot.states import OrderFlow

logger = logging.getLogger(__name__)
router = Router(name="orders")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🚫 Текущий черновик наряда очищен. Присылай новые фото, когда будешь готов.")


@router.message(F.photo | F.document, OrderFlow.confirming)
async def photo_while_confirming(message: Message) -> None:
    await message.answer("⏳ Предыдущий наряд ждёт подтверждения — нажми кнопку под сводкой выше или /cancel.")


@router.message(F.photo | F.document)
async def receive_photo(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    draft_id = data.get("draft_id") or uuid.uuid4().hex[:10]
    photos = list(data.get("photos", []))

    dest_dir = settings.data_dir / "incoming" / draft_id
    path = await download_order_photo(message.bot, message, dest_dir, settings.telegram_max_file_mb)

    photos.append(str(path))
    await state.update_data(draft_id=draft_id, photos=photos)
    await state.set_state(OrderFlow.collecting)

    await message.answer(
        f"📸 Принято ({len(photos)} шт.). Пришли ещё фото/PDF или жми «Готово».",
        reply_markup=kb.collecting_keyboard(),
    )


@router.callback_query(F.data == kb.CANCEL_DRAFT_CB)
async def cancel_draft(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("🚫 Наряд отменён. Файлы остались на диске, но обработаны не будут.")
    await callback.answer()


@router.callback_query(F.data == kb.DONE_CB)
async def photos_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("photos"):
        await callback.answer("Сначала пришли хотя бы одно фото.", show_alert=True)
        return
    await state.set_state(OrderFlow.choosing_addresses)
    await callback.message.edit_text("📍 Сколько дополнительных адресов (точек) в маршруте?")
    await callback.message.answer("Выбери количество:", reply_markup=kb.addresses_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith(kb.ADDRESSES_CB_PREFIX))
async def addresses_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    num_addresses = int(callback.data.removeprefix(kb.ADDRESSES_CB_PREFIX))
    await state.update_data(num_addresses=num_addresses)
    await callback.message.edit_text(
        f"📍 Адресов: {num_addresses}\n\n🕵️ Сканировать открытый наряд в 1С на дубликаты услуг?"
    )
    await callback.message.answer("Выбери:", reply_markup=kb.scan_1c_keyboard())
    await callback.answer()


@router.callback_query(F.data.in_({kb.SCAN_YES_CB, kb.SCAN_NO_CB}))
async def scan_chosen_and_run_pipeline(
    callback: CallbackQuery, state: FSMContext, settings: Settings, order_store: OrderStore
) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    num_addresses = data.get("num_addresses", 0)

    await callback.message.edit_text("⏳ Обрабатываю бланки (Gemini)... это может занять до минуты.")
    await callback.answer()

    booked_in_1c = []
    if callback.data == kb.SCAN_YES_CB:
        booked_in_1c = await asyncio.to_thread(capture_booked_items_screenshot)

    result = await asyncio.to_thread(run_pipeline, [Path(p) for p in photos], num_addresses, booked_in_1c)

    order_id = uuid.uuid4().hex[:8].upper()
    write_audit_log(result, order_id)
    chat_id = callback.message.chat.id
    await order_store.create_pending(order_id, chat_id, photos, num_addresses, result.order_data, result.summary)

    await state.update_data(order_id=order_id)
    await state.set_state(OrderFlow.confirming)

    text = format_order_summary(result.summary, order_id=order_id)
    if settings.onec_dry_run:
        text += "\n\n🧪 <i>Режим dry-run: ввод в 1С будет только залогирован, реальных кликов не будет.</i>"
    await callback.message.answer(text, reply_markup=kb.confirm_keyboard(order_id), parse_mode="HTML")


@router.callback_query(F.data.startswith(kb.CONFIRM_ENTER_CB))
async def confirm_enter(
    callback: CallbackQuery, state: FSMContext, settings: Settings, order_store: OrderStore
) -> None:
    order_id = callback.data.removeprefix(kb.CONFIRM_ENTER_CB)
    record = await order_store.get(order_id)
    if not record or record.status != PENDING:
        await callback.answer("Этот наряд уже обработан или не найден.", show_alert=True)
        return

    await callback.message.edit_text(f"⚡️ Вношу наряд #{order_id} в 1С...")
    await callback.answer()

    bot_1c = OneCOrderEntryBot(settings=settings)
    try:
        entered = await asyncio.to_thread(bot_1c.enter_order, record.order_data)
    except WorkflowError as e:
        await order_store.set_status(order_id, FAILED, error_message=str(e))
        await callback.message.answer(
            f"{e.user_message}\n\n⚠️ Наряд #{order_id} внесён частично или не внесён — "
            "файлы НЕ перемещены, проверь окно 1С вручную перед повтором."
        )
        await state.clear()
        return

    _move_photos_to_processed(record.photo_paths, settings)
    await order_store.set_status(order_id, ENTERED)
    await callback.message.answer(f"✅ Наряд #{order_id} внесён в 1С ({len(entered)} позиций).")
    await state.clear()


@router.callback_query(F.data.startswith(kb.CONFIRM_CANCEL_CB))
async def confirm_cancel(callback: CallbackQuery, state: FSMContext, order_store: OrderStore) -> None:
    order_id = callback.data.removeprefix(kb.CONFIRM_CANCEL_CB)
    await order_store.set_status(order_id, CANCELLED, error_message="Отменено пользователем перед вводом в 1С")
    await callback.message.edit_text(f"🚫 Наряд #{order_id} отменён. Файлы остались в data/incoming — не обработаны.")
    await callback.answer()
    await state.clear()


@router.callback_query(F.data.startswith(kb.SHOW_LOG_CB))
async def show_log(callback: CallbackQuery, settings: Settings) -> None:
    order_id = callback.data.removeprefix(kb.SHOW_LOG_CB)
    log_path = settings.output_dir / f"order_{order_id}.json"
    if not log_path.exists():
        await callback.answer("Лог не найден.", show_alert=True)
        return
    await callback.message.answer_document(FSInputFile(log_path))
    await callback.answer()


def _move_photos_to_processed(photo_paths: list[str], settings: Settings) -> None:
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    for path_str in photo_paths:
        src = Path(path_str)
        if not src.exists():
            continue
        try:
            shutil.move(str(src), str(settings.processed_dir / src.name))
        except OSError as e:
            logger.error(f"❌ Ошибка перемещения {src.name}: {e}")
