"""One-off maintenance script: regenerates `search_key` / `dropdown_index`
abbreviations in `catalog_1c_mapping` from each item's `name`. Run after
manually editing catalog.json's coffin/wreath/cross/etc. entries:
`python tools/upgrade_catalog.py`.

(Previously lived at src/upgrade_catalog.py with a path bug that pointed at
src/data/catalog.json instead of the real data/catalog.json — moved here
since nothing in src/ imports it, it's an operator-run tool, not library code.)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.settings import get_settings


def upgrade_catalog() -> None:
    catalog_path = get_settings().catalog_path
    with open(catalog_path, encoding='utf-8') as f:
        catalog = json.load(f)

    mapping = catalog.get("catalog_1c_mapping", {})

    for items in mapping.values():
        # Группируем по поисковому ключу, чтобы правильно посчитать стрелочки вниз
        search_groups: dict[str, int] = {}

        for item in items:
            name = item["name"]
            words = name.split()

            # Генерируем твою аббревиатуру (Например: "Труна Ровко" -> "Труна Р")
            if len(words) >= 2:
                # Очищаем от случайных кавычек в начале второго слова
                second_word_clean = ''.join([c for c in words[1] if c.isalpha()])
                search_key = f"{words[0]} {second_word_clean[0]}" if second_word_clean else f"{words[0]} {words[1][0]}"
            else:
                search_key = words[0] if words else ""

            item["search_key"] = search_key

            # Считаем, какой это элемент по счету в выпадающем списке 1С
            item["dropdown_index"] = search_groups.get(search_key, 0)
            search_groups[search_key] = item["dropdown_index"] + 1

    with open(catalog_path, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"✅ Каталог ({catalog_path}) успешно прокачан аббревиатурами и индексами!")


if __name__ == "__main__":
    upgrade_catalog()
