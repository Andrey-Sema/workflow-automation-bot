"""Admin control panel: order/system status, tariff edits, catalog reload,
runtime operator allowlist. All commands here require `is_admin=True`,
injected by `AccessControlMiddleware`.
"""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src import catalog_admin, config
from src.errors import AccessDeniedError
from src.operators_store import OperatorsStore
from src.order_store import OrderStore
from src.rdp_status import describe_rdp_status
from src.settings import Settings

router = Router(name="admin")


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise AccessDeniedError("Эта команда доступна только администраторам.")


@router.message(Command("status"))
async def cmd_status(message: Message, settings: Settings, order_store: OrderStore) -> None:
    recent = await order_store.recent(limit=5)
    lines = [
        f"🤖 Каталог: {'✅ загружен' if config.catalog_is_loaded() else '❌ НЕ загружен'} "
        f"({len(config.SERVICES_LIST)} услуг)",
        f"⌨️ Ввод в 1С: {'🧪 dry-run (тест)' if settings.onec_dry_run else '🔴 боевой режим'}",
        f"🖥️ RDP-сессия: {describe_rdp_status(settings)}",
        "",
        "📋 Последние наряды:",
    ]
    if not recent:
        lines.append("  (пока пусто)")
    for r in recent:
        lines.append(f"  #{r.order_id} · {r.status} · {r.deceased_fio or '?'} · {r.calculated_total} грн")
    await message.answer("\n".join(lines))


@router.message(Command("tariffs"))
async def cmd_tariffs(message: Message, is_admin: bool) -> None:
    _require_admin(is_admin)
    tariffs = catalog_admin.get_editable_tariffs()
    lines = ["💰 Текущие тарифы и правила копки:", ""]
    lines.extend(f"  {key} = {value}" for key, value in tariffs.items())
    lines.append("")
    lines.append("Изменить: /set_tariff <ключ> <значение>")
    await message.answer("\n".join(lines))


@router.message(Command("set_tariff"))
async def cmd_set_tariff(message: Message, command: CommandObject, is_admin: bool) -> None:
    _require_admin(is_admin)
    args = (command.args or "").split()
    if len(args) != 2:
        await message.answer("Использование: /set_tariff <ключ> <значение>\nСписок ключей: /tariffs")
        return

    key, raw_value = args
    try:
        value = int(raw_value)
    except ValueError:
        await message.answer("⚠️ Значение должно быть целым числом.")
        return

    try:
        catalog_admin.update_tariff(key, value)
    except ValueError as e:
        await message.answer(f"⚠️ {e}")
        return

    await message.answer(f"✅ {key} = {value}")


@router.message(Command("reload_catalog"))
async def cmd_reload_catalog(message: Message, is_admin: bool) -> None:
    _require_admin(is_admin)
    catalog_admin.reload_catalog()
    report = config.check_catalog()
    suffix = ""
    if not report.ok:
        suffix = f"\n❌ Ошибок в каталоге: {len(report.errors)} — смотри /catalog_check"
    elif report.warnings:
        suffix = f"\n⚠️ Замечаний: {len(report.warnings)} — смотри /catalog_check"
    await message.answer(f"🔄 Каталог перезагружен: {len(config.SERVICES_LIST)} услуг.{suffix}")


@router.message(Command("catalog_check"))
async def cmd_catalog_check(message: Message, is_admin: bool) -> None:
    """Reports structural errors and mapping ambiguities in catalog.json.

    Worth running after every catalog edit: the problems it lists (two items
    at one price, one dropdown slot claimed by two names) don't fail loudly
    at runtime — they make the bot type a confidently wrong nomenclature
    line into 1C, which has no undo.
    """
    _require_admin(is_admin)
    report = config.check_catalog()

    header = "📖 <b>Проверка каталога</b>"
    if report.stats:
        header += (
            f"\nпозиций: {report.stats.get('mapping_items', 0)} в "
            f"{report.stats.get('mapping_categories', 0)} категориях · "
            f"услуг в справочнике: {report.stats.get('services_list', 0)}"
        )
    header += f"\nошибок: {len(report.errors)} · предупреждений: {len(report.warnings)}"

    body = escape(report.as_text(limit=25))
    # Telegram caps a message at 4096 chars; a catalog with many collisions
    # can blow past that, and a failed send would look like the command
    # itself is broken.
    text = f"{header}\n\n<pre>{body}</pre>"
    if len(text) > 4000:
        text = f"{header}\n\n<pre>{body[:3500]}\n…</pre>\n(полный отчёт: python -m src.catalog_schema)"
    await message.answer(text)


@router.message(Command("operators"))
async def cmd_operators(message: Message, is_admin: bool, settings: Settings, operators_store: OperatorsStore) -> None:
    _require_admin(is_admin)
    env_ops = settings.telegram_operator_ids or "—"
    runtime_ops = operators_store.list() or "—"
    await message.answer(f"👥 Операторы:\n  Из .env (постоянные): {env_ops}\n  Добавлены в рантайме: {runtime_ops}")


@router.message(Command("add_operator"))
async def cmd_add_operator(
    message: Message, command: CommandObject, is_admin: bool, operators_store: OperatorsStore
) -> None:
    _require_admin(is_admin)
    try:
        chat_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("Использование: /add_operator <chat_id>")
        return
    operators_store.add(chat_id)
    await message.answer(f"✅ Оператор {chat_id} добавлен.")


@router.message(Command("remove_operator"))
async def cmd_remove_operator(
    message: Message, command: CommandObject, is_admin: bool, operators_store: OperatorsStore
) -> None:
    _require_admin(is_admin)
    try:
        chat_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("Использование: /remove_operator <chat_id>")
        return
    removed = operators_store.remove(chat_id)
    if removed:
        await message.answer(f"✅ Оператор {chat_id} удалён из рантайм-списка.")
    else:
        await message.answer("⚠️ Такого оператора нет в рантайм-списке (проверь, не задан ли он в .env).")
