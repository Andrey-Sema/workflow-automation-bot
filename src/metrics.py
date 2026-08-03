"""Prometheus metrics for the pipeline, Gemini calls, 1C entry, RDP
connectivity and Telegram access control. Exposed over HTTP by
`src/telegram_bot/metrics_server.py`.

Kept as one flat module of module-level metric objects (the standard
prometheus_client pattern) rather than instrumenting call sites with
ad-hoc counters, so every metric name and label set is visible in one
place instead of scattered across the codebase.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PIPELINE_RUNS = Counter(
    "workflow_pipeline_runs_total",
    "Order pipeline runs (photos -> Gemini -> validated order)",
    ["outcome"],  # success | gemini_error | validation_error
)

PIPELINE_DURATION = Histogram(
    "workflow_pipeline_duration_seconds",
    "Time to run the full digitization pipeline for one order",
    buckets=(1, 2.5, 5, 10, 20, 30, 60, 120, 180),
)

GEMINI_CALLS = Counter(
    "workflow_gemini_calls_total",
    "Gemini API calls, one per attempt (including retries)",
    ["agent", "outcome"],  # agent: vision | logic | booked_ocr; outcome: success | error
)

GEMINI_CALL_DURATION = Histogram(
    "workflow_gemini_call_duration_seconds",
    "Gemini API call latency per successful attempt",
    ["agent"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60),
)

ONEC_ENTRIES = Counter(
    "workflow_onec_entries_total",
    "1C наряд entry attempts",
    ["outcome"],  # success | failed | rdp_disconnected
)

ONEC_ENTRY_DURATION = Histogram(
    "workflow_onec_entry_duration_seconds",
    "Time to type one full наряд into 1C",
    buckets=(1, 5, 10, 20, 30, 60, 120, 300),
)

ONEC_ITEMS_ENTERED = Counter(
    "workflow_onec_items_entered_total",
    "Individual service/good/transport line items typed into 1C",
)

RDP_CONNECTED = Gauge(
    "workflow_rdp_connected",
    "1 if the RDP session to the 1C host is connected, 0 otherwise",
)

GEMINI_CIRCUIT_BREAKER_OPEN = Gauge(
    "workflow_gemini_circuit_breaker_open",
    "1 if the shared Gemini circuit breaker is currently open (short-circuiting calls), 0 otherwise",
)

TELEGRAM_ACCESS = Counter(
    "workflow_telegram_access_total",
    "Telegram updates, by whether the access-control allowlist let them through",
    ["result"],  # allowed | denied
)

ORDERS_PENDING = Gauge(
    "workflow_orders_pending",
    "Orders currently awaiting operator confirmation",
)
