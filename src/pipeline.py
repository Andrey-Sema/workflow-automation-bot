"""Shared orchestration used by both the CLI (`main.py`) and the Telegram
bot: photos -> Gemini agents -> Pydantic validation -> conflict summary.

Having one shared entry point (instead of duplicating this in the CLI and
the bot) is also what fixes a real bug: `apply_business_rules_in_python`
expects `booked_in_1c` to be a plain list of name strings, but
`capture_booked_items_screenshot()` returns a list of dicts
(`{"name": ..., "quantity": ..., ...}`). The old CLI passed the dicts
straight through, which crashed the business-rules step with an
`AttributeError` on every order where a 1C duplicate-scan actually found
something. `run_pipeline()` normalizes that boundary once, here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from src.ai_parser import parse_images_with_gemini
from src.errors import GeminiParsingError, ValidationFailedError
from src.order_conflicts import OrderSummary, build_order_summary
from src.settings import get_settings
from src.validator import validate_and_fix_order

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    raw_json_str: str
    order_data: dict
    summary: OrderSummary
    elapsed_seconds: float


def _booked_item_names(booked_in_1c: list) -> list[str]:
    return [item["name"] if isinstance(item, dict) else str(item) for item in booked_in_1c]


def run_pipeline(
    photo_paths: list[Path | str],
    num_addresses: int,
    booked_in_1c: list | None = None,
) -> PipelineResult:
    """Runs the full digitization pipeline for one order.

    Raises `GeminiParsingError` / `ValidationFailedError` (both
    `WorkflowError` subclasses with a friendly `.user_message`) instead of
    returning falsy sentinels, so callers (CLI, Telegram handlers) have one
    consistent way to report failure to the user.
    """
    start = time.time()
    booked_names = _booked_item_names(booked_in_1c or [])
    str_paths = [str(p) for p in photo_paths]

    raw_json_str, final_json_data = parse_images_with_gemini(str_paths, num_addresses, booked_names)
    if not final_json_data:
        raise GeminiParsingError("Пайплайн Gemini вернул пустые данные после всех попыток")

    order_data = validate_and_fix_order(final_json_data)
    if not order_data:
        raise ValidationFailedError("Данные не прошли Pydantic-валидацию")

    summary = build_order_summary(order_data)
    elapsed = time.time() - start
    logger.info(f"⏱ Пайплайн отработал за {elapsed:.2f} сек.")

    return PipelineResult(raw_json_str=raw_json_str, order_data=order_data, summary=summary, elapsed_seconds=elapsed)


def write_audit_log(result: PipelineResult, order_id: str) -> Path:
    """Persists the raw + final JSON for one order, for later audit/debugging."""
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.output_dir / f"order_{order_id}.json"
    payload = {
        "order_id": order_id,
        "raw": _safe_json_loads(result.raw_json_str),
        "final": result.order_data,
    }
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return log_path


def _safe_json_loads(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
