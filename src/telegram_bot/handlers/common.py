from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="common")

HELP_TEXT = """\
🤖 <b>Workflow Automation Bot</b>

<b>Как оформить наряд:</b>
1. Пришли сюда фото бланка (можно несколько, и PDF тоже подходит).
2. Нажми «✅ Готово, обрабатываем».
3. Укажи количество дополнительных адресов в маршруте.
4. Выбери, сканировать ли открытый наряд в 1С на дубликаты.
5. Бот пришлёт сводку: ФИО, сумму на бланке и по расчёту, и список \
строк, которые нужно проверить вручную.
6. Нажми «✅ Ввести в 1С» — или «❌ Отмена», если что-то не так.

<b>Команды:</b>
/cancel — сбросить текущий черновик наряда
/status — статус системы и последние наряды
/help — эта справка

<b>Для администраторов:</b>
/tariffs, /set_tariff, /reload_catalog, /operators, /add_operator, /remove_operator

<b>Важно:</b> отмена наряда после кнопки «✅ Ввести в 1С» невозможна — \
1С не даёт бота откатить уже введённые строки. Проверяй сводку внимательно \
до подтверждения.
"""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Не понял 🤔 Пришли фото бланка, чтобы начать наряд, или /help за инструкцией.")
