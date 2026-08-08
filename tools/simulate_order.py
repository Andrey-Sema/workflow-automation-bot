"""Offline end-to-end dry run of one наряд — no Gemini, no RDP, no 1C.

Takes the JSON that Agent 1 (Vision) would produce from the photos and
pushes it through every deterministic stage the real bot uses:

    business rules -> Pydantic validation -> conflict summary -> 1C typing

The 1C stage runs `OneCOrderEntryBot` in dry-run, so the output is literally
the keystroke plan for the наряд: which tab, which search key, how many
Down presses, what quantity.

Use it to see what the current rules do with a real order before touching
production, and to check whether a catalog edit fixed the line you meant:

    python tools/simulate_order.py tests/fixtures/order_base.json
    python tools/simulate_order.py tests/fixtures/order_base.json --catalog data/catalog.json
    python tools/simulate_order.py tests/fixtures/order_base.json --addresses 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import agent_logic, config  # noqa: E402
from src.errors import OneCInputError  # noqa: E402
from src.onec_order_entry import CATEGORY_TAB_MAP, OneCOrderEntryBot, OrderCategory  # noqa: E402
from src.order_conflicts import build_order_summary  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.validator import validate_and_fix_order  # noqa: E402

DEFAULT_CATALOG = Path("data/catalog.sample.json")
DEFAULT_ORDER = Path("tests/fixtures/order_base.json")


def _rule(title: str) -> None:
    print(f"\n{'─' * 100}\n{title}\n{'─' * 100}")


def _print_lines(order: dict) -> None:
    header = f"{'категория':<10} {'позиция после правил':<46} {'кол':>4} {'сумма':>8} {'1С search_key':<16} {'↓':>3}"
    print(header)
    print("─" * len(header))
    for category in ("services", "goods", "transport"):
        for item in order.get(category, []):
            key = item.get("1c_search_key") or "—"
            down = item.get("1c_down_presses", 0) if item.get("1c_search_key") else "—"
            name = item.get("name", "?")
            print(
                f"{category:<10} {name[:45]:<46} {item.get('quantity', 1):>4} "
                f"{item.get('price', 0):>8} {key[:15]:<16} {down:>3}"
            )


def simulate(order_path: Path, catalog_path: Path, num_addresses: int, booked: list[str]) -> int:
    config.load_catalog_from(catalog_path)
    agent_logic.refresh_from_catalog()

    raw = json.loads(order_path.read_text(encoding="utf-8"))
    raw.pop("_meta", None)

    _rule(f"1. ВХОД (что вернул бы Агент-1 Vision) — {order_path}")
    counts = {c: len(raw.get(c, [])) for c in ("services", "goods", "transport")}
    print(f"каталог: {catalog_path} · позиций в наряде: {counts} · доп. адресов: {num_addresses}")
    print(f"сумма в бланке: {raw.get('handwritten_total', 0)} грн")

    _rule("2. БИЗНЕС-ПРАВИЛА + МАППИНГ В 1С")
    processed = agent_logic.apply_business_rules_in_python(raw, num_addresses, booked)
    _print_lines(processed)

    _rule("3. ВАЛИДАЦИЯ (Pydantic)")
    order = validate_and_fix_order(processed)
    print(f"позиций после валидации: "
          f"{ {c: len(order.get(c, [])) for c in ('services', 'goods', 'transport')} }")

    _rule("4. СВОДКА ДЛЯ ОПЕРАТОРА")
    summary = build_order_summary(order)
    print(f"покойный:        {summary.deceased_fio}")
    print(f"заказчик:        {summary.customer_fio}")
    print(f"кладбище:        {summary.cemetery}")
    print(f"сумма в бланке:  {summary.handwritten_total} грн")
    print(f"сумма расчётная: {summary.calculated_total} грн")
    print(f"расхождение:     {summary.sum_diff} грн "
          f"({'⚠️ ВЫШЕ ДОПУСКА' if summary.sum_mismatch else '✅ в пределах допуска'})")
    print(f"нет даты рожд.:  {'⚠️ да' if summary.missing_birth_date else 'нет'}")
    print(f"автоввод в 1С:   {'✅ возможен целиком' if summary.can_auto_enter else '⛔ заблокирован'}")

    if summary.warnings:
        print(f"\nпредупреждения правил ({len(summary.warnings)}):")
        for w in summary.warnings:
            print(f"  · {w}")

    if summary.unmapped_items:
        print(f"\n⛔ БЕЗ СООТВЕТСТВИЯ В 1С — только ручной ввод ({len(summary.unmapped_items)}):")
        for name in summary.unmapped_items:
            print(f"  · {name}")

    _rule("5. ПЛАН ВВОДА В 1С (dry-run, ни одного реального нажатия)")
    for category in OrderCategory:
        print(f"  вкладка {CATEGORY_TAB_MAP[category]:<18} <- {category.value}")
    print()

    bot = OneCOrderEntryBot(settings=Settings(onec_dry_run=True))
    mapped = {
        c: [i for i in order.get(c, []) if i.get("1c_search_key")]
        for c in ("services", "goods", "transport")
    }
    try:
        entered = bot.enter_order(mapped)
    except OneCInputError as e:
        print(f"⛔ {e}")
        return 1

    total_lines = sum(len(v) for v in mapped.values())
    print(f"\n✅ 1С получила бы {len(entered)} строк из {sum(len(order.get(c, [])) for c in mapped)} "
          f"({total_lines} с соответствием, {len(summary.unmapped_items)} вручную)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("order", nargs="?", type=Path, default=DEFAULT_ORDER, help="JSON наряда (выход Vision)")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="файл каталога")
    parser.add_argument("--addresses", type=int, default=0, help="доп. адресов на маршруте")
    parser.add_argument("--booked", nargs="*", default=[], help="позиции, уже вбитые в 1С")
    args = parser.parse_args()
    return simulate(args.order, args.catalog, args.addresses, args.booked)


if __name__ == "__main__":
    raise SystemExit(main())
