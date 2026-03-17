import os

# Файлы и папки, которые НЕ надо копировать
EXCLUDE_DIRS = {'.venv', '__pycache__', '.git', 'data', '.hypothesis', 'debug_screenshots'}
EXCLUDE_FILES = {'dump_project.py', 'full_project_code.txt', '.env'}
# Какие расширения берем
INCLUDE_EXT = {'.py', '.txt', '.md', '.bat', '.gitignore'}


def get_project_tree(startpath):
    """Генерирует текстовое дерево проекта для контекста."""
    tree = ["Структура проекта:"]
    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * level
        tree.append(f"{indent}{os.path.basename(root)}/")
        sub_indent = ' ' * 4 * (level + 1)
        for f in files:
            if f not in EXCLUDE_FILES and any(f.endswith(ext) for ext in INCLUDE_EXT):
                tree.append(f"{sub_indent}{f}")
    return "\n".join(tree)


def collect_code():
    project_root = "."  # Запускаем из корня проекта
    output_file = "full_project_code.txt"

    with open(output_file, "w", encoding="utf-8") as out:
        # 1. Записываем дерево проекта
        out.write(get_project_tree(project_root))
        out.write("\n\n" + "=" * 50 + "\nСОДЕРЖИМОЕ ФАЙЛОВ\n" + "=" * 50 + "\n")

        # 2. Обходим все файлы
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

            for file in files:
                if file in EXCLUDE_FILES:
                    continue

                if any(file.endswith(ext) for ext in INCLUDE_EXT):
                    path = os.path.join(root, file)
                    relative_path = os.path.relpath(path, project_root)

                    out.write(f"\n\n{'=' * 20}\nFILE: {relative_path}\n{'=' * 20}\n\n")
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            out.write(f.read())
                    except Exception as e:
                        out.write(f"Ошибка чтения файла: {e}")


if __name__ == "__main__":
    collect_code()
    print("✅ Дамп готов! Ищи full_project_code.txt в корне проекта.")