"""Fills in missing `search_key` / `dropdown_index` in `catalog_1c_mapping`.

**Read this before running it.** `search_key` is what an operator types into
1C's nomenclature search box and `dropdown_index` is how many times they
press Down afterwards. Both are facts about the 1C window, not about
catalog.json — nothing in the JSON can derive them, so this script only
*guesses* an abbreviation from the item name.

That is why the default mode is `--check` (report, write nothing), and why
the guess never overwrites a value that is already there. The previous
version regenerated everything unconditionally, which against the real
catalog rewrote 50 of 281 entries: it flattened the hand-tuned service
indices (снос 9/10/12 all became 0) and produced keys that are punctuation
rather than searchable text ("Корзина (", "Отче 2", "Табличка -"). Anything
typed from those lands on the wrong nomenclature line in 1C — silently,
with no undo.

    python tools/upgrade_catalog.py                       # report only
    python tools/upgrade_catalog.py --fill                # fill blanks only
    python tools/upgrade_catalog.py --regenerate --yes    # rewrite everything
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.settings import get_settings  # noqa: E402


def guess_search_key(name: str) -> str:
    """"Труна Ровно 4 гран." -> "Труна Р".

    Skips over words that carry no letters ("(№", "9/2", "-"), because an
    abbreviation ending in punctuation is not something an operator can type
    into 1C's search box.
    """
    words = name.split()
    if not words:
        return ""

    head = words[0]
    for word in words[1:]:
        letters = "".join(c for c in word if c.isalpha())
        if letters:
            return f"{head} {letters[0]}"
    return head


def plan(catalog: dict, *, regenerate: bool) -> list[tuple[str, str, str, int, str, int]]:
    """Returns [(category, name, old_key, old_index, new_key, new_index), ...]
    for entries this run would change."""
    changes = []

    for category, items in catalog.get("catalog_1c_mapping", {}).items():
        # Indices are only ever assigned within one search_key's group, and
        # existing indices are treated as taken so a generated one can't
        # collide with a hand-tuned neighbour.
        taken: dict[str, set[int]] = defaultdict(set)
        for item in items:
            if item.get("search_key") and not regenerate:
                taken[item["search_key"]].add(int(item.get("dropdown_index", 0) or 0))

        for item in items:
            old_key = item.get("search_key", "")
            old_index = int(item.get("dropdown_index", 0) or 0)
            has_key = bool(old_key.strip())

            if has_key and not regenerate:
                continue

            new_key = guess_search_key(item["name"])
            if not new_key:
                continue

            used = taken[new_key]
            new_index = 0
            while new_index in used:
                new_index += 1
            used.add(new_index)

            if new_key != old_key or new_index != old_index:
                changes.append((category, item["name"], old_key, old_index, new_key, new_index))

    return changes


def apply(catalog: dict, changes: list) -> None:
    index = {(c, n): (k, i) for c, n, _, _, k, i in changes}
    for category, items in catalog.get("catalog_1c_mapping", {}).items():
        for item in items:
            key = (category, item["name"])
            if key in index:
                item["search_key"], item["dropdown_index"] = index[key]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, default=None, help="путь к каталогу (по умолчанию data/catalog.json)")
    parser.add_argument("--fill", action="store_true", help="записать только пустые search_key")
    parser.add_argument("--regenerate", action="store_true", help="перегенерировать ВСЕ ключи (разрушительно)")
    parser.add_argument("--yes", action="store_true", help="подтвердить --regenerate без вопроса")
    args = parser.parse_args()

    path = args.catalog or get_settings().catalog_path
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))

    changes = plan(catalog, regenerate=args.regenerate)
    mode = "ПЕРЕГЕНЕРАЦИЯ ВСЕГО" if args.regenerate else "заполнение пустых"
    print(f"📖 {path} · режим: {mode} · затронуто позиций: {len(changes)}\n")

    for category, name, old_key, old_index, new_key, new_index in changes:
        print(f"  [{category}] {name[:50]:<52} {old_key!r} #{old_index}  ->  {new_key!r} #{new_index}")

    if not changes:
        print("  (нечего менять)")
        return 0

    if not (args.fill or args.regenerate):
        print("\n🔍 Это только отчёт. Записать: --fill (безопасно) или --regenerate --yes (перезапишет ручную настройку).")
        return 0

    if args.regenerate and not args.yes:
        print("\n⛔ --regenerate затирает вручную выставленные search_key/dropdown_index. Добавь --yes, если уверен.")
        return 1

    apply(catalog, changes)
    Path(path).write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ Записано в {path}: {len(changes)} позиций. Проверь: python -m src.catalog_schema {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
