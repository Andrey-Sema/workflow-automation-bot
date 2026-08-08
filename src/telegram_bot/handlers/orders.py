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
import re
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
from src.rdp_status import is_rdp_connected
from src.settings import Settings
from src.summary_formatting import format_order_summary
from src.telegram_bot import keyboards as kb
from src.telegram_bot.file_intake import download_order_photo
from src.telegram_bot.locks import chat_locks as _chat_locks
from src.telegram_bot.locks import onec_entry_lock as _onec_entry_lock
from src.telegram_bot.states import OrderFlow

logger = logging.getLogger(__name__)
router = Router(name="orders")

# Everything below treats `callback.data` as untrusted input. It looks like
# it comes from our own inline keyboards, but nothing stops a Telegram
# client from sending an arbitrary callback payload for any chat it is in —
# so the ids and numbers carried in it get validated, not just parsed.

# Order ids are minted as `uuid4().hex[:8].upper()` (see
# scan_chosen_and_run_pipeline), so anything else is forged or stale.
_ORDER_ID_RE = re.compile(r"^[0-9A-F]{8}$")

# Matches the choices addresses_keyboard() actually renders.
MAX_EXTRA_ADDRESSES = 7

# One наряд is a handful of pages. The cap exists because every accepted
# file is written to disk and later sent to Gemini: without it, a stuck
# "resend" loop on an operator's phone fills the data volume and burns the
# API quota with no natural stopping point.
MAX_PHOTOS_PER_ORDER = 30


def _validated_order_id(callback_data: str | None, prefix: str) -> str | None:
    order_id = (callback_data or "").removeprefix(prefix)
    return order_id if _ORDER_ID_RE.match(order_id) else None


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🚫 Текущий черновик наряда очищен. Присылай новые фото, когда будешь готов.")


@router.message(F.photo | F.document, OrderFlow.confirming)
async def photo_while_confirming(message: Message) -> None:
    await message.answer("⏳ Предыдущий наряд ждёт подтверждения — нажми кнопку под сводкой выше или /cancel.")


@router.message(F.photo | F.document)
async def receive_photo(message: Message, state: FSMContext, settings: Settings) -> None:
    # Telegram delivers a multi-photo selection as several separate updates
    # in quick succession; without this lock, two concurrent calls for the
    # same chat can both read `photos` before either writes it back and one
    # photo silently vanishes from the draft. See _chat_locks comment above.
    async with _chat_locks[message.chat.id]:
        data = await state.get_data()
        draft_id = data.get("draft_id") or uuid.uuid4().hex[:10]
        photos = list(data.get("photos", []))

        if len(photos) >= MAX_PHOTOS_PER_ORDER:
            await message.answer(
                f"⚠️ В одном наряде не больше {MAX_PHOTOS_PER_ORDER} файлов. "
                "Жми «Готово» либо /cancel, чтобы начать заново."
            )
            return

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
    async with _chat_locks[callback.message.chat.id]:
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
    # `int()` on raw callback data accepted anything: "order:addr:999999999"
    # multiplied straight into the extra-point quantities, producing a наряд
    # with absurd line totals, and a negative value silently skipped the
    # surcharge entirely. Only the values the keyboard offers are accepted.
    raw = callback.data.removeprefix(kb.ADDRESSES_CB_PREFIX)
    if not raw.isdigit() or not (0 <= int(raw) <= MAX_EXTRA_ADDRESSES):
        logger.warning("Отклонён некорректный выбор адресов: %r", callback.data)
        await callback.answer("Некорректный выбор. Нажми кнопку ещё раз.", show_alert=True)
        return
    num_addresses = int(raw)

    async with _chat_locks[callback.message.chat.id]:
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

    if not settings.onec_dry_run and not is_rdp_connected(settings):
        await callback.answer("🖥️ Нет связи с 1С по RDP — ввод сейчас невозможен. Попробуй позже.", show_alert=True)
        return

    await callback.answer()
    if _onec_entry_lock.locked():
        await callback.message.edit_text(
            f"⏳ Наряд #{order_id} в очереди — сейчас в 1С вносится другой наряд, жди..."
        )

    # Serializes against every other confirm_enter() call, not just ones
    # for this order: only one наряд can physically be typed into the 1C
    # window at a time. Re-check status *after* acquiring the lock too — a
    # double-tap (or two operators confirming the same order) could have
    # both passed the fast check above before either actually committed.
    async with _onec_entry_lock:
        record = await order_store.get(order_id)
        if not record or record.status != PENDING:
            await callback.message.edit_text("Этот наряд уже обработан (обрабатывался параллельно).")
            return

        await callback.message.edit_text(f"⚡️ Вношу наряд #{order_id} в 1С...")

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
    record = await order_store.get(order_id)
    if not record or record.status != PENDING:
        await callback.answer("Этот наряд уже обработан или не найден.", show_alert=True)
        return

    await callback.answer()
    if _onec_entry_lock.locked():
        await callback.message.edit_text(
            f"⏳ Наряд #{order_id} сейчас вносится в 1С — дожидаюсь окончания, чтобы отменить..."
        )

    # Same race confirm_enter() guards against, mirrored: ✅ and ❌ sit on the
    # same message, so a tap on this button can land while another task is
    # mid-way through actually typing this order into 1C. Without sharing
    # the lock and re-checking status, a cancel here could overwrite an
    # ENTERED record back to CANCELLED even though the physical 1C entry
    # already happened and can't be un-typed.
    async with _onec_entry_lock:
        record = await order_store.get(order_id)
        if not record or record.status != PENDING:
            await callback.message.edit_text(f"Наряд #{order_id} уже обработан — отмена не применена.")
            return
        await order_store.set_status(order_id, CANCELLED, error_message="Отменено пользователем перед вводом в 1С")
        await callback.message.edit_text(
            f"🚫 Наряд #{order_id} отменён. Файлы остались в data/incoming — не обработаны."
        )
        await state.clear()


@router.callback_query(F.data.startswith(kb.SHOW_LOG_CB))
async def show_log(callback: CallbackQuery, settings: Settings, order_store: OrderStore) -> None:
    """Sends the raw+final JSON for one order back into the chat that owns it.

    `callback.data` is attacker-controlled — Telegram clients may send any
    callback payload, not only the ones our keyboards offer — so the order
    id is validated before it ever reaches the filesystem. Interpolating it
    straight into a path (as this did) let `order:log:x/../../../catalog`
    resolve to `/catalog.json` and mail any readable `.json` on the box back
    to the sender, comfortably inside Telegram's 64-byte callback limit.
    """
    order_id = _validated_order_id(callback.data, kb.SHOW_LOG_CB)
    if order_id is None:
        await callback.answer("Лог не найден.", show_alert=True)
        return

    # The order must exist and belong to this chat: order ids are short, and
    # without this any operator could read another operator's наряд —
    # including the deceased's and customer's personal data — by guessing.
    record = await order_store.get(order_id)
    if record is None or record.chat_id != callback.message.chat.id:
        await callback.answer("Лог не найден.", show_alert=True)
        return

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
