import os

# Файлы и папки, которые НЕ надо копировать
EXCLUDE = {'.venv', '__pycache__', '.env', '.git', 'data', 'dump_project.py'}


def collect_code():
    with open("src/full_project_code.txt", "w", encoding="utf-8") as out:
        for root, dirs, files in os.walk("src"):
            # Убираем лишние папки из обхода
            dirs[:] = [d for d in dirs if d not in EXCLUDE]

            for file in files:
                if file.endswith(".py") or file == "requirements.txt":
                    path = os.path.join(root, file)
                    out.write(f"\n\n{'=' * 20}\nFILE: {path}\n{'=' * 20}\n\n")
                    with open(path, "r", encoding="utf-8") as f:
                        out.write(f.read())


if __name__ == "__main__":
    collect_code()
    print("✅ Готово! Весь проект в файле full_project_code.txt")