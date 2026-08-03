"""Verifies pipeline/agent/1C/telegram code paths actually move the
Prometheus counters they're supposed to. Metric objects are global
singletons (like any prometheus_client app), so tests check relative
deltas rather than absolute values to stay order-independent.
"""

import json
from unittest.mock import MagicMock, patch

from src import metrics
from src.errors import OneCInputError


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def test_pipeline_runs_counter_increments_on_success():
    before = _counter_value(metrics.PIPELINE_RUNS, outcome="success")

    from src import pipeline

    with patch("src.pipeline.parse_images_with_gemini", return_value=("{}", {"deceased": {"fio": "Тест"}})):
        pipeline.run_pipeline(["a.jpg"], num_addresses=0)

    assert _counter_value(metrics.PIPELINE_RUNS, outcome="success") == before + 1


def test_pipeline_runs_counter_increments_on_gemini_error():
    import pytest

    from src import pipeline
    from src.errors import GeminiParsingError

    before = _counter_value(metrics.PIPELINE_RUNS, outcome="gemini_error")

    with patch("src.pipeline.parse_images_with_gemini", return_value=("", {})), pytest.raises(GeminiParsingError):
        pipeline.run_pipeline(["a.jpg"], num_addresses=0)

    assert _counter_value(metrics.PIPELINE_RUNS, outcome="gemini_error") == before + 1


def test_gemini_calls_counter_increments_for_vision_agent(tmp_path):
    from PIL import Image

    from src.agent_vision import extract_raw_data

    path = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10)).save(path)

    before = _counter_value(metrics.GEMINI_CALLS, agent="vision", outcome="success")

    mock_response = MagicMock()
    mock_response.text = json.dumps({"deceased": {"fio": "Тест"}})
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("src.agent_vision.get_client", return_value=mock_client):
        extract_raw_data([str(path)])

    assert _counter_value(metrics.GEMINI_CALLS, agent="vision", outcome="success") == before + 1


def test_onec_entries_counter_increments_on_success():
    from src.onec_order_entry import OneCOrderEntryBot
    from src.settings import Settings

    before_entries = _counter_value(metrics.ONEC_ENTRIES, outcome="success")
    before_items = metrics.ONEC_ITEMS_ENTERED._value.get()

    bot = OneCOrderEntryBot(settings=Settings(gemini_api_key="dummy", onec_dry_run=True))
    bot.enter_order({
        "services": [{"name": "Катафалк", "price": 2000, "1c_search_key": "Катафалк"}],
        "goods": [], "transport": [],
    })

    assert _counter_value(metrics.ONEC_ENTRIES, outcome="success") == before_entries + 1
    assert metrics.ONEC_ITEMS_ENTERED._value.get() == before_items + 1


def test_onec_entries_counter_increments_on_failure():
    import pytest

    from src.onec_order_entry import OneCOrderEntryBot
    from src.settings import Settings

    before = _counter_value(metrics.ONEC_ENTRIES, outcome="failed")

    bot = OneCOrderEntryBot(settings=Settings(gemini_api_key="dummy", onec_dry_run=True))
    with pytest.raises(OneCInputError):
        bot.enter_order({"services": [{"name": "no key"}], "goods": [], "transport": []})

    assert _counter_value(metrics.ONEC_ENTRIES, outcome="failed") == before + 1


def test_onec_entries_counter_increments_on_rdp_disconnected(tmp_path):
    import pytest

    from src.errors import RdpConnectionError
    from src.onec_order_entry import OneCOrderEntryBot
    from src.settings import Settings

    before = _counter_value(metrics.ONEC_ENTRIES, outcome="rdp_disconnected")

    settings = Settings(gemini_api_key="dummy", onec_dry_run=False, data_dir=tmp_path)
    bot = OneCOrderEntryBot(settings=settings)
    with pytest.raises(RdpConnectionError):
        bot.enter_order({"services": [], "goods": [], "transport": []})

    assert _counter_value(metrics.ONEC_ENTRIES, outcome="rdp_disconnected") == before + 1


async def test_telegram_access_counter_increments(tmp_path):
    from unittest.mock import AsyncMock

    from src.operators_store import OperatorsStore
    from src.settings import Settings
    from src.telegram_bot.middlewares import AccessControlMiddleware

    before_allowed = _counter_value(metrics.TELEGRAM_ACCESS, result="allowed")
    before_denied = _counter_value(metrics.TELEGRAM_ACCESS, result="denied")

    settings = Settings(gemini_api_key=None, telegram_operator_ids=[1])
    middleware = AccessControlMiddleware(settings, OperatorsStore(tmp_path / "operators.json"))

    from types import SimpleNamespace
    allowed_event = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())
    denied_event = SimpleNamespace(chat=SimpleNamespace(id=999), answer=AsyncMock())

    await middleware(AsyncMock(), allowed_event, {})
    await middleware(AsyncMock(), denied_event, {})

    assert _counter_value(metrics.TELEGRAM_ACCESS, result="allowed") == before_allowed + 1
    assert _counter_value(metrics.TELEGRAM_ACCESS, result="denied") == before_denied + 1
