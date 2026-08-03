"""Renders an `OrderSummary` as a Telegram HTML message.

All dynamic text (names, item names extracted by OCR) is HTML-escaped —
these values ultimately come from a photographed handwritten form, which is
untrusted input as far as Telegram's HTML parse mode is concerned.
"""

from __future__ import annotations

from html import escape

from src.order_conflicts import OrderSummary


def format_order_summary(summary: OrderSummary, *, order_id: str) -> str:
    lines: list[str] = []

    lines.append(f"📋 <b>Наряд {escape(order_id)}</b>")
    lines.append(f"⚰️ Покойный: <b>{escape(summary.deceased_fio)}</b>")
    if summary.customer_fio:
        lines.append(f"👤 Заказчик: {escape(summary.customer_fio)}")
    if summary.cemetery:
        lines.append(f"🌳 Кладбище: {escape(summary.cemetery)}")

    lines.append("")
    lines.append(
        f"📊 Позиций: услуги {summary.services_count} · "
        f"товары {summary.goods_count} · транспорт {summary.transport_count}"
    )
    lines.append(f"💰 Сумма на бланке: <b>{summary.handwritten_total} грн</b>")
    lines.append(f"🧮 Сумма по расчёту: <b>{summary.calculated_total} грн</b>")

    if summary.sum_mismatch:
        sign = "+" if summary.sum_diff > 0 else ""
        lines.append(f"🚨 <b>Расхождение сумм: {sign}{summary.sum_diff} грн!</b>")
    elif summary.handwritten_total > 0:
        lines.append("✅ Суммы совпадают")
    else:
        lines.append("⚠️ Итоговая сумма на бланке не распознана")

    if summary.missing_birth_date:
        lines.append("🚨 Дата рождения не указана на бланке")

    if summary.unmapped_items:
        lines.append("")
        lines.append("🔧 <b>Строки, требующие ручной проверки (нет соответствия в каталоге 1С):</b>")
        for name in summary.unmapped_items:
            lines.append(f"  • {escape(name)}")

    if summary.warnings:
        lines.append("")
        lines.append("ℹ️ <b>Автоматически применённые правила:</b>")
        for warning in summary.warnings:
            lines.append(f"  • {escape(warning)}")

    lines.append("")
    if summary.has_conflicts:
        lines.append("⚠️ Есть конфликты — проверь перед вводом в 1С.")
    else:
        lines.append("✅ Конфликтов не найдено.")

    return "\n".join(lines)
