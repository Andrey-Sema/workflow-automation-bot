@echo off
set PYTHONUTF8=1
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==========================================
echo 🤖 Workflow Automation Bot v2.0 | Startup
echo ==========================================

:: 1. Проверка: не запущен ли уже бот?
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | find /I "main.py" >NUL
IF "!ERRORLEVEL!"=="0" (
    echo ⚠️ ВНИМАНИЕ! Бот уже запущен в другом окне.
    echo Заверши старый процесс, прежде чем запускать новый.
    pause
    exit /b 1
)

:: 2. Проверка наличия Python в системе
python --version >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Python не найден! Установи Python 3.10+ и добавь его в PATH.
    pause
    exit /b 1
)

:: 3. Создаем структуру папок
if not exist "data\input" mkdir "data\input"
if not exist "data\output" mkdir "data\output"
if not exist "data\processed" mkdir "data\processed"
if not exist "data\templates" mkdir "data\templates"

:: 4. Проверка конфига (.env)
IF NOT EXIST ".env" (
    echo ⚠️ Файл .env не найден! Создаю шаблон...
    echo GEMINI_API_KEY=твой_ключ_сюда > .env
    echo 🔑 Я создал файл .env. Вставь туда свой API ключ и запусти снова.
    pause
    exit /b 1
)

:: 5. Инициализация / Обновление окружения
IF NOT EXIST ".venv" (
    echo 📦 Окружение не найдено. Создаю .venv...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo ❌ Ошибка при создании .venv!
        pause
        exit /b 1
    )
)

:: Всегда проверяем зависимости (pip сам поймет, если всё уже установлено)
echo 🛠️ Проверка зависимостей из requirements.txt...
".\.venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Ошибка при установке библиотек! Проверь интернет и requirements.txt.
    pause
    exit /b 1
)
echo ✅ Окружение готово.

echo ==========================================
echo 🚀 ЗАПУСК СИСТЕМЫ...
echo ==========================================

:: Запуск основного скрипта
".\.venv\Scripts\python.exe" main.py

set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% NEQ 0 (
    echo.
    echo ❌ Бот завершился с ошибкой (код: %EXIT_CODE%)
) else (
    echo.
    echo ✅ Бот успешно завершил работу.
)

pause
exit /b %EXIT_CODE%