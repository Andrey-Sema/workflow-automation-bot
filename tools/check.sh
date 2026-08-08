#!/usr/bin/env bash
# Полный прогон проекта: линтеры, безопасность, зависимости, тесты, и
# прогон трёх реальных нарядов через весь пайплайн офлайн.
#
# Идемпотентен: два запуска подряд на неизменном коде обязаны давать
# идентичный вывод (кроме времени). Это и проверяет шаг «идемпотентность» —
# наряды прогоняются дважды и результаты сравниваются побайтово.
#
#   ./tools/check.sh            # полный прогон
#   ./tools/check.sh --quick    # без pip-audit (не ходит в сеть)
set -uo pipefail
cd "$(dirname "$0")/.."

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

FAILED=0
step() { printf '\n\033[1m════ %s ════\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✅ %s\033[0m\n' "$1"; }
bad()  { printf '  \033[31m❌ %s\033[0m\n' "$1"; FAILED=$((FAILED + 1)); }

run() { # run "название" команда...
    local name="$1"; shift
    if out=$("$@" 2>&1); then ok "$name"; else bad "$name"; echo "$out" | tail -25; fi
}

step "1/8  Линтер (ruff + flake8-bandit)"
run "ruff check" uv run --frozen ruff check .

step "2/8  Статический анализ безопасности (bandit)"
if uv run --frozen bandit -c pyproject.toml -q -r src/ main.py docker/ 2>&1 | grep -q "^>>"; then
    bad "bandit нашёл проблемы"
    uv run --frozen bandit -c pyproject.toml -q -r src/ main.py docker/ 2>&1 | head -30
else
    ok "bandit: 0 issues"
fi

step "3/8  Зависимости"
run "uv.lock синхронен с pyproject.toml" uv lock --check
if [ "$QUICK" = "0" ]; then
    if uv run --frozen pip-audit -r requirements.txt 2>&1 | grep -q "No known vulnerabilities"; then
        ok "pip-audit: известных CVE нет"
    else
        bad "pip-audit нашёл уязвимости"
        uv run --frozen pip-audit -r requirements.txt --desc 2>&1 | head -20
    fi
else
    printf '  ⏭  pip-audit пропущен (--quick)\n'
fi

step "4/8  Тесты и покрытие"
if out=$(xvfb-run -a uv run --frozen pytest tests/ -q \
          --cov=src --cov-report=term --cov-fail-under=88 2>&1); then
    ok "$(echo "$out" | grep -E '^[0-9]+ passed' | tail -1)"
    ok "$(echo "$out" | grep -oE 'Total coverage: [0-9.]+%' | tail -1)"
else
    bad "тесты или порог покрытия"
    echo "$out" | tail -30
fi

step "5/8  Валидация боевого каталога"
if out=$(uv run --frozen python -m src.catalog_schema data/catalog.sample.json 2>&1); then
    ok "структура каталога валидна ($(echo "$out" | grep -c '⚠️') предупреждений)"
else
    bad "каталог не проходит валидацию"; echo "$out" | head -20
fi

step "6/8  Наряды через пайплайн + идемпотентность"
for f in tests/fixtures/order_*.json; do
    name=$(basename "$f" .json)
    a=$(LOG_LEVEL=ERROR uv run --frozen python tools/simulate_order.py "$f" 2>/dev/null)
    b=$(LOG_LEVEL=ERROR uv run --frozen python tools/simulate_order.py "$f" 2>/dev/null)
    if [ -z "$a" ]; then
        bad "$name: пайплайн ничего не вернул"
    elif [ "$a" != "$b" ]; then
        bad "$name: НЕ ИДЕМПОТЕНТЕН — два прогона дали разный результат"
        diff <(echo "$a") <(echo "$b") | head -20
    else
        ok "$name: $(echo "$a" | grep -oE 'расхождение: .*' | head -1)"
    fi
done

step "7/8  Импорт без дисплея (headless)"
if out=$(env -u DISPLAY uv run --frozen python -c "
import src.telegram_bot.bot, src.agent_booked_ocr, src.win_1c_bot, src.onec_order_entry
assert src.win_1c_bot.pyautogui is None
print('ok')" 2>&1); then
    ok "модули импортируются без DISPLAY и деградируют мягко"
else
    bad "падение при импорте без DISPLAY"; echo "$out" | tail -15
fi

step "8/8  Конфигурация деплоя"
if ! command -v docker >/dev/null 2>&1; then
    printf '  ⏭  docker недоступен — шаг пропущен\n'
# docker-compose.yml объявляет `env_file: .env`, поэтому compose требует сам
# файл, а не переменные окружения. Если своего .env нет — делаем временный из
# примера и обязательно убираем за собой, включая аварийный выход.
elif [ -f .env ]; then
    run "docker compose config" docker compose config
else
    trap 'rm -f .env' EXIT INT TERM
    { cat .env.example; printf '\nGRAFANA_ADMIN_PASSWORD=checkonly\n'; } > .env
    run "docker compose config (на временном .env)" docker compose config
    rm -f .env
    trap - EXIT INT TERM
fi

printf '\n\033[1m'
if [ "$FAILED" = "0" ]; then printf '\033[32m══ ПРОГОН ЧИСТЫЙ ══\033[0m\n'; else printf '\033[31m══ ПРОБЛЕМ: %s ══\033[0m\n' "$FAILED"; fi
exit "$FAILED"
