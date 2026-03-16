@echo off
set PYTHONUTF8=1
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==========================================
echo 🚀 Инициализация Nier:Automato...
echo ==========================================

:: 1. Проверка на дурака: не запущен ли уже бот?
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | find /I "main.py" >NUL
IF "!ERRORLEVEL!"=="0" (
    echo ⚠️ БЛЯТЬ! Бот уже запущен! Заверши предыдущий процесс.
    pause
    exit /b 1
)

:: 2. Проверка наличия Python
python --version >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Python не установлен или не добавлен в PATH! Пиздуй устанавливать.
    pause
    exit /b 1
)

:: 3. Создаем структуру папок (чтобы код не падал при первом запуске на новом компе)
IF NOT EXIST "data\input" mkdir "data\input"
IF NOT EXIST "data\output" mkdir "data\output"
IF NOT EXIST "data\processed" mkdir "data\processed"
IF NOT EXIST "data\templates" mkdir "data\templates"

:: 4. Проверка ключей (.env)
IF NOT EXIST ".env" (
    echo ⚠️ Ебать-копать, файла .env нет! Создаю шаблон...
    echo GEMINI_API_KEY=твой_ключ_сюда > .env
    echo ⚠️ Я создал файл .env. Открой его, вставь свой API ключ от Gemini и запусти снова!
    pause
    exit /b 1
)

:: 5. Поднятие окружения
IF NOT EXIST ".venv" (
    echo ⚠️ Окружение не найдено. Создаю с нуля, подожди...
    python -m venv .venv
    IF %ERRORLEVEL% NEQ 0 (
        echo ❌ Ошибка создания виртуального окружения!
        pause
        exit /b 1
    )
    echo ✅ Папка .venv создана.

    echo 📦 Ставлю зависимости из requirements.txt...
    ".\.venv\Scripts\python.exe" -m pip install --upgrade pip >NUL
    ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
    IF %ERRORLEVEL% NEQ 0 (
        echo ❌ Пиздец, ошибка установки зависимостей! Проверь инет или файл requirements.txt.
        pause
        exit /b 1
    )
    echo ✅ Все нужные либы установлены!
) ELSE (
    echo ✅ Виртуальное окружение на месте.
)
echo ==========================================
echo 🤖 ЗАПУСК СИСТЕМЫ...
echo ==========================================

:: Запускаем через вызов модуля, так надежнее
".\.venv\Scripts\python.exe" main.py

:: Сохраняем код ошибки, используя кавычки для безопасности
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% NEQ 0 (
    echo.
    echo ❌ Бот завершился с ошибкой (код: %EXIT_CODE%)
) else (
    echo.
    echo ✅ Бот успешно завершил работу
)

pause
exit /b %EXIT_CODE%