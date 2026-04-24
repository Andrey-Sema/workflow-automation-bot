import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "data" / "catalog.json"


def upgrade_catalog():
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        catalog = json.load(f)

    mapping = catalog.get("catalog_1c_mapping", {})

    for category, items in mapping.items():
        # Группируем по поисковому ключу, чтобы правильно посчитать стрелочки вниз
        search_groups = {}

        for item in items:
            name = item["name"]
            words = name.split()

            # Генерируем твою аббревиатуру (Например: "Труна Ровко" -> "Труна Р")
            if len(words) >= 2:
                # Очищаем от случайных кавычек в начале второго слова
                second_word_clean = ''.join([c for c in words[1] if c.isalpha()])
                if second_word_clean:
                    search_key = f"{words[0]} {second_word_clean[0]}"
                else:
                    search_key = f"{words[0]} {words[1][0]}"
            else:
                search_key = words[0] if words else ""

            item["search_key"] = search_key

            # Считаем, какой это элемент по счету в выпадающем списке 1С
            if search_key not in search_groups:
                search_groups[search_key] = 0

            item["dropdown_index"] = search_groups[search_key]
            search_groups[search_key] += 1

    # Перезаписываем JSON
    with open(CATALOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print("✅ Каталог успешно прокачан аббревиатурами и индексами!")


if __name__ == "__main__":
    upgrade_catalog()