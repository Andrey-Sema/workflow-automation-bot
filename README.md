# 🤖 Workflow Automation Bot (v3.0)

Business process automation for a funeral service agency: a Telegram bot receives
photos of handwritten order forms, a multi-agent Gemini pipeline digitizes and
normalizes them against a services catalog, an operator confirms a summary, and
the order is typed into a legacy 1C accounting system running on a remote
Windows machine over RDP.

## Architecture

Everything ships as **one Docker container** — the AI/business-logic layer and
the low-level 1C input automation run together, because the input automation
only works against a real, rendered RDP session:

```
┌─────────────────────────────────────────────────────────────────┐
│ Docker container                                                 │
│                                                                    │
│  Xvfb (virtual display) ← xfreerdp3 renders the remote Windows/1C │
│         │                  desktop onto it, auto-reconnects        │
│         │                                                          │
│         ├── src/win_1c_bot.py — pyautogui screen-template          │
│         │   matching + clicks, driving the Xvfb display exactly    │
│         │   as it would a real local screen                        │
│         │   exactly as it would a real local screen                │
│         │                                                          │
│         └── src/telegram_bot/ — aiogram bot: file intake, order    │
│             wizard, confirmation, admin panel                      │
│                                                                    │
│  Telegram user ──▶ bot ──▶ Gemini pipeline ──▶ Pydantic validator  │
│                     │              │                                │
│                     │        data/catalog.json (single source      │
│                     │        of truth for services/tariffs)         │
│                     ▼                                               │
│              summary + ✅/❌ ──▶ OneCOrderEntryBot ──▶ 1C via RDP  │
└─────────────────────────────────────────────────────────────────┘
```

**Why one container, not split services:** the only reliable way to enter
data into 1C is to drive the same pixels a human would see, so the RDP
client has to render into a display the automation code can see too. Xvfb
provides that display inside the container; `src/win_1c_bot.py`'s core
technique — locate a saved template image on screen, click its center — is
the original, proven approach, kept as-is. It's now more resilient around
that core (retries a transient miss, distinguishes "template file missing
from disk" from "not on screen right now", tolerates pyautogui being
unavailable instead of crashing at import time) but drives the same pixels
the same way, whether that's a virtual screen or a physical one.

## 🌟 Pipeline

- **Agent 1 (Vision)** — `gemini-3.6-flash`, digitizes the handwritten form
  (handwriting, abbreviations, unstructured lists) into structured JSON.
- **Agent 2 (Logic)** — `gemini-3.5-flash-lite` + deterministic Python business
  rules (`src/agent_logic.py`): name normalization against
  `data/catalog.json`, tariff math (extra points, digging/towel splits,
  quantity healing), 1C search-key/dropdown-index mapping.
- **Agent 3 (1C duplicate scan)** — `gemini-3.5-flash-lite`, reads whatever the
  1C "Услуги" table currently shows on the RDP desktop to avoid double-entry.
- **Validator** — strict Pydantic v2 schemas, integer-overflow guards, sum
  reconciliation.
- **1C entry** — `src/onec_order_entry.py`, typed input via the same
  `pyautogui` technique as the tab-switching code, **dry-run by default**.

All three Gemini calls use `response_mime_type="application/json"` for
reliable structured output. Prompts are otherwise unchanged from the
original, proven wording.

## 🗂 Single source of truth: `data/catalog.json`

All service names, tariffs, digging rules and 1C nomenclature mappings come
from this file; nothing is hardcoded elsewhere. The working copy at
`data/catalog.json` stays gitignored, and **`data/catalog.sample.json` is a
committed copy of the real catalog** — use it as the reference structure, as
a test fixture, or as the starting point for a fresh deployment:

```bash
cp data/catalog.sample.json data/catalog.json
```

```json
{
  "tariffs": {"extra_point": 500, "transport_base": 1000, "snos_base": 1550},
  "digging_rules": {"kopka_person_count": 4, "base_price_per_person": 1925, "towel_prices": [1400]},
  "personnel_packages": {"6200": {"name": "снос", "qty": 4}},
  "known_unit_prices": {"Хусточки": [40]},
  "catalog_1c_mapping": {
    "coffins": [{"name": "...", "price": 0, "search_key": "...", "dropdown_index": 0, "aliases": []}]
  },
  "services_list": ["Катафалк", "..."]
}
```

**Validate it before trusting it.** Ambiguities in this file don't fail
loudly — they make the bot type a confidently wrong nomenclature line into
1C, which has no undo. Two items sharing a price in one category, or one
dropdown slot claimed by two names, are reported by:

```bash
python -m src.catalog_schema data/catalog.json     # or /catalog_check in the bot
```

The same check runs at load time and at startup, so problems land in the log
rather than in 1C.

**`aliases`** is how you record a mapping no string comparison could infer —
the form says «Послуги персоналу для поховання», 1C calls it «снос
(Галстук)». Without one, such a line matches on price alone and is refused
(see `docs/BUSINESS_LOGIC.md`, rules M3 / M-R2).

Admins can tweak `tariffs`/`digging_rules` at runtime via the bot
(`/set_tariff`) without restarting; edits are written back to
`catalog.json` and hot-reloaded (see `src/catalog_admin.py`). Editing
`services_list` / `catalog_1c_mapping` still means hand-editing the file,
then `/reload_catalog`.

## 📐 Business rules

Every decision that moves money or reaches 1C is made by deterministic
Python, not by the model. **`docs/BUSINESS_LOGIC.md` is the full inventory**
— tariff maths, the digging/towel split, personnel packages, catalog
matching and every refusal rule, plus where to add new conditions.

Three real orders are committed as fixtures under `tests/fixtures/`
(personal data replaced, every sum and quantity as written on the form).
Run any of them through the whole chain offline — no Gemini, no RDP, no 1C:

```bash
python tools/simulate_order.py tests/fixtures/order_base.json
python tools/simulate_order.py tests/fixtures/order_vip.json --addresses 2
```

It prints the per-line mapping, the operator summary, and the exact
keystroke plan 1C would receive.

## 🚀 Getting started

1. `cp .env.example .env` and fill in `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_ADMIN_IDS`, and the `RDP_*` variables for the Windows/1C host.
2. Drop your real `data/catalog.json` and the 1C tab template screenshots in
   `data/templates/` (already present for `tab_uslugi.png` /
   `tab_sklad.png` / `tab_prochie.png` — recapture if your 1C layout differs,
   using `tools/calibrate_tab_templates.py`).
3. **First run:** `mkdir -p data && sudo chown -R 1000:1000 data` — the
   container runs as a fixed non-root UID 1000, and `./data` is bind-mounted,
   so the host directory needs matching ownership (or `chmod 777 data`, if
   fixed ownership isn't practical on your host).
4. `docker compose up -d --build`
5. Message your bot on Telegram: `/start`.

Keep `ONEC_DRY_RUN=true` (the default) until an operator has confirmed the
field navigation in `src/onec_order_entry.py` (`FieldNav`) against the real
1C window — dry-run only logs what it would type, no real clicks/keystrokes.
Set `DEBUG_VNC=true` to watch the automation live at `http://<host>:6080`.

### Secrets

`RDP_PASSWORD_FILE` (a Docker secret mounted read-only) is preferred over
`RDP_PASSWORD` in `.env` when possible. Either way the password only ever
reaches `xfreerdp3` over a stdin pipe (`/from-stdin`) — never as a CLI
argument, so it doesn't show up in `ps aux` inside the container.

## 🤖 Using the bot

1. Send photos of the order form (or a PDF) — as many as needed.
2. Tap **✅ Готово, обрабатываем**.
3. Pick the number of extra addresses on the route.
4. Choose whether to scan the open 1C наряд for duplicates.
5. The bot replies with a summary: ФИО, sum on the form vs. the calculated
   sum, and any conflicting lines (catalog mismatches, missing birth date,
   sum discrepancies beyond 1%).
6. **✅ Ввести в 1С** to commit, **❌ Отмена** to discard, or **📄 Подробный
   лог** for the full raw+final JSON. Cancellation before entry is always
   safe; there is no "undo" after commit — 1C has no API to roll back
   individual typed rows, so review the summary carefully first.

Admin-only: `/status`, `/tariffs`, `/set_tariff <key> <value>`,
`/reload_catalog`, `/catalog_check`, `/operators`, `/add_operator <id>`,
`/remove_operator <id>`.

## 🛡 Resilience & safety

- **RDP safety gate** — real (non-dry-run) 1C entry is refused outright while
  `.rdp_status` isn't `connected`: blind keystrokes into a screen that isn't
  showing 1C are worse than not typing at all (`src/rdp_status.py`).
- **Graceful shutdown** — on SIGTERM the bot stops taking updates but waits
  up to `SHUTDOWN_GRACE_SECONDS` (default 45s) for an in-flight 1C entry to
  finish before exiting; `docker/entrypoint.py` keeps Xvfb/FreeRDP alive
  until the bot process is done, and compose's `stop_grace_period: 60s`
  gives the whole dance room to complete.
- **Single-writer 1C lock** — one shared lock serializes every ✅/❌ action
  against the physical 1C window; double-taps and stale buttons re-check the
  order's status after acquiring it, so an already-entered наряд can't be
  re-entered or flipped back to "cancelled".
- **Gemini circuit breaker** — after 5 consecutive API failures all agents
  fail fast for 60s instead of burning full retry/backoff cycles per order
  (`src/circuit_breaker.py`).
- **Callback throttling** — repeated button taps within
  `TELEGRAM_THROTTLE_SECONDS` (default 0.7s) are dropped. Deliberately *not*
  applied to messages: Telegram delivers a multi-photo album as separate
  updates milliseconds apart.
- **Persistent FSM storage** — the order wizard's state lives in SQLite
  (`data/fsm_storage.db`), so a container restart doesn't lose an operator's
  half-collected draft (`src/telegram_bot/sqlite_storage.py`).
- **Startup preflight** — token/API-key/data-dir/catalog checks run before
  polling starts, so misconfiguration fails loudly at boot instead of at the
  first order (`src/preflight.py`).

## 📊 Monitoring (Prometheus + Grafana)

The bot exposes Prometheus metrics on `:9090/metrics` (toggle with
`METRICS_ENABLED` / `METRICS_PORT`): pipeline runs/durations, per-agent
Gemini call outcomes and latencies, 1C entry outcomes and typed-item counts,
RDP connectivity, circuit-breaker state, pending orders, and allowed/denied
Telegram access.

`docker compose up -d` also starts:

- **prometheus** — scrapes the bot every 15s, 30-day retention
  (`monitoring/prometheus.yml`); not published to the host by default.
- **grafana** — `http://<host>:3000`, admin password from
  `GRAFANA_ADMIN_PASSWORD` in `.env` (default `admin` — change it). The
  "Workflow Automation Bot" dashboard is provisioned automatically
  (`monitoring/grafana/dashboards/workflow-bot.json`).

Run only the bot with `docker compose up -d bot` if you don't want the
monitoring stack.

## 🛠 Tech stack

| Category      | Tools |
|----------------|-------|
| Language       | Python 3.11+ |
| AI             | `google-genai`, Gemini 3.6 Flash / 3.5 Flash-Lite |
| Bot            | aiogram 3.x, aiosqlite (order/session persistence) |
| Validation     | Pydantic v2, `pydantic-settings` |
| Automation     | PyAutoGUI + OpenCV (template-match confidence), FreeRDP (`xfreerdp3`), Xvfb |
| Quality        | ruff, bandit, pytest, pytest-cov, Hypothesis (property-based testing) |
| Monitoring     | prometheus-client, aiohttp (`/metrics`), Prometheus, Grafana |
| Deployment     | Docker (single image: Xvfb + FreeRDP + bot), docker-compose |

## 📁 Layout

```
src/
  settings.py, errors.py, logging_setup.py   # cross-cutting foundation
  config.py, catalog_admin.py,               # catalog.json load/reload/edit
    catalog_schema.py                        #   + structural/semantic validation
  gemini_client.py                           # lazy Gemini client factory
  agent_vision.py, agent_logic.py,           # the 3-agent pipeline
    agent_booked_ocr.py, ai_parser.py
  validator.py                               # Pydantic schemas
  pipeline.py                                # shared orchestration (CLI + bot)
  order_conflicts.py, summary_formatting.py  # confirmation summary
  order_store.py, operators_store.py         # sqlite / JSON persistence
  rdp_status.py, preflight.py                # RDP safety gate, startup checks
  circuit_breaker.py, metrics.py             # resilience + Prometheus metric defs
  win_1c_bot.py                              # low-level 1C input (core technique unchanged)
  onec_order_entry.py                        # 1C order-entry automation (new)
  telegram_bot/                              # aiogram bot: handlers, middlewares, keyboards,
                                             #   sqlite FSM storage, /metrics server
docker/entrypoint.py                         # Xvfb + FreeRDP supervisor + bot launcher
monitoring/                                  # Prometheus config + Grafana provisioning/dashboard
docs/BUSINESS_LOGIC.md                       # every business rule, and where to add new ones
data/catalog.sample.json                     # committed copy of the real catalog
tools/                                       # calibration scripts + simulate_order.py (offline E2E)
tests/fixtures/                              # three real orders as pipeline fixtures
tests/                                       # pytest + Hypothesis
```

## 🧪 Development

```bash
python3 -m venv --system-site-packages .venv   # --system-site-packages: see Dockerfile comment on python3-tk
. .venv/bin/activate
pip install -r requirements-dev.txt

ruff check .
bandit -c pyproject.toml -r src/ main.py docker/
xvfb-run -a pytest tests/ --cov=src --cov-report=term-missing
```

`--system-site-packages` matters: `pyautogui` imports `mouseinfo`, which
hard-requires a real `tkinter` binding at import time. `apt install
python3-tk` only matches the *same* interpreter build it was compiled
against — a separately-built Python (e.g. `python:3.13-slim`,
`actions/setup-python`) has no matching package and the import fails
outright. Using the OS's own `python3` + `python3-tk` (as this venv command,
the Dockerfile, and CI all do) sidesteps that entirely.

## ⚠️ Known limitations

- **No post-commit undo.** Cancelling only works before "✅ Ввести в 1С" —
  see "Using the bot" above.
- **Field navigation needs on-site calibration.** `FieldNav` in
  `src/onec_order_entry.py` (Tab counts between Nomenclature → Quantity →
  Price, whether Price auto-fills) is a best-effort default; confirm against
  the real 1C window before disabling `ONEC_DRY_RUN`.
- **RDP/Docker build couldn't be end-to-end tested in the environment this
  was built in** (no network access to pull base images, no real RDP/1C
  host to connect to). `docker compose config` validates cleanly and every
  pure-logic piece (command construction, credential precedence, field
  sequencing in dry-run) has unit test coverage, but a real `docker compose
  up` against your actual Windows/1C host is the first thing to try after
  deploying.
