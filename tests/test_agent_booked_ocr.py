import json
from unittest.mock import MagicMock, patch

from PIL import Image

from src.agent_booked_ocr import capture_booked_items_screenshot, validate_items


def test_validate_items_filters_junk():
    raw = [
        {"name": "Катафалк", "quantity": 1, "price": 2000, "sum": 2000},
        {"name": "X"},  # too short, no letters check fails anyway
        {"name": "12345"},  # no alpha chars
        "not a dict",
        {"name": "Катафалк", "quantity": 1, "price": 2000, "sum": 2000},  # duplicate name
    ]
    result = validate_items(raw)
    assert len(result) == 1
    assert result[0]["name"] == "Катафалк"


def test_validate_items_recomputes_wrong_sum():
    raw = [{"name": "Пронос труни", "quantity": 3, "price": 100, "sum": 999}]
    result = validate_items(raw)
    assert result[0]["sum"] == 300


def test_validate_items_keeps_zero_sum_when_price_or_qty_missing():
    raw = [{"name": "Пронос труни", "quantity": 0, "price": 0, "sum": 0}]
    result = validate_items(raw)
    assert result[0]["quantity"] == 1  # clamped to at least 1
    assert result[0]["sum"] == 0


@patch("src.agent_booked_ocr.get_client")
@patch("src.agent_booked_ocr.pyautogui")
def test_capture_booked_items_happy_path(mock_pyautogui, mock_get_client, tmp_path, monkeypatch):
    # Patch get_settings() to return an isolated Settings instance, rather
    # than clearing the process-wide lru_cache: that leaks into any other
    # test that holds a reference to the previously-cached singleton
    # (e.g. src.config._settings) and breaks test isolation.
    from src.settings import Settings
    monkeypatch.setattr(
        "src.agent_booked_ocr.get_settings", lambda: Settings(gemini_api_key="dummy", data_dir=tmp_path)
    )

    fake_screenshot = Image.new("RGB", (100, 100), color="white")
    mock_pyautogui.screenshot.return_value = fake_screenshot

    mock_response = MagicMock()
    mock_response.text = json.dumps([{"name": "Катафалк", "quantity": 1, "price": 2000, "sum": 2000}])
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    items = capture_booked_items_screenshot()

    assert items == [{"name": "Катафалк", "quantity": 1, "price": 2000, "sum": 2000}]
    assert (tmp_path / "debug_screenshots").exists()


def test_prune_old_screenshots_keeps_only_newest(tmp_path):
    from src.agent_booked_ocr import MAX_DEBUG_SCREENSHOTS, _prune_old_screenshots

    for i in range(MAX_DEBUG_SCREENSHOTS + 7):
        (tmp_path / f"screenshot_20260101_{i:06d}.jpg").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_text("keep me")

    _prune_old_screenshots(tmp_path)

    remaining = sorted(tmp_path.glob("screenshot_*.jpg"))
    assert len(remaining) == MAX_DEBUG_SCREENSHOTS
    # the oldest (lowest timestamp) ones are the ones deleted
    assert remaining[0].name == "screenshot_20260101_000007.jpg"
    assert (tmp_path / "unrelated.txt").exists()


@patch("src.agent_booked_ocr.pyautogui")
def test_capture_booked_items_returns_empty_on_screenshot_failure(mock_pyautogui):
    mock_pyautogui.screenshot.side_effect = RuntimeError("no display")
    assert capture_booked_items_screenshot() == []


@patch("src.agent_booked_ocr.get_client")
@patch("src.agent_booked_ocr.pyautogui")
def test_capture_booked_items_returns_empty_on_bad_gemini_response(
    mock_pyautogui, mock_get_client, tmp_path, monkeypatch
):
    from src.settings import Settings
    monkeypatch.setattr(
        "src.agent_booked_ocr.get_settings", lambda: Settings(gemini_api_key="dummy", data_dir=tmp_path)
    )

    mock_pyautogui.screenshot.return_value = Image.new("RGB", (10, 10))
    mock_response = MagicMock()
    mock_response.text = "not json at all"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    assert capture_booked_items_screenshot() == []
