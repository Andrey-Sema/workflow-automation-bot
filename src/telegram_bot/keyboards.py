from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DONE_CB = "order:done"
CANCEL_DRAFT_CB = "order:cancel_draft"
ADDRESSES_CB_PREFIX = "order:addr:"
SCAN_YES_CB = "order:scan:yes"
SCAN_NO_CB = "order:scan:no"
CONFIRM_ENTER_CB = "order:confirm:"
CONFIRM_CANCEL_CB = "order:reject:"
SHOW_LOG_CB = "order:log:"


def collecting_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово, обрабатываем", callback_data=DONE_CB)],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=CANCEL_DRAFT_CB)],
    ])


def addresses_keyboard() -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(text=str(n), callback_data=f"{ADDRESSES_CB_PREFIX}{n}") for n in range(4)]
    row2 = [InlineKeyboardButton(text=str(n), callback_data=f"{ADDRESSES_CB_PREFIX}{n}") for n in range(4, 8)]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])


def scan_1c_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕵️ Да, сканировать 1С", callback_data=SCAN_YES_CB),
            InlineKeyboardButton(text="⏭ Нет, пропустить", callback_data=SCAN_NO_CB),
        ],
    ])


def confirm_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ввести в 1С", callback_data=f"{CONFIRM_ENTER_CB}{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CONFIRM_CANCEL_CB}{order_id}")],
        [InlineKeyboardButton(text="📄 Подробный лог", callback_data=f"{SHOW_LOG_CB}{order_id}")],
    ])
