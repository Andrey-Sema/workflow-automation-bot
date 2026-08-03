import io
import json
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.agent_vision import (
    extract_raw_data,
    fix_image_orientation,
    optimize_image,
    optimize_image_bytes,
    prepare_input_files,
    validate_extracted_data,
)


def _make_image(path, size=(50, 50), mode="RGB"):
    img = Image.new(mode, size, color=(120, 40, 200))
    img.save(path)
    return path


def test_fix_image_orientation_returns_image_without_exif():
    img = Image.new("RGB", (10, 10))
    result = fix_image_orientation(img)
    assert result.size == (10, 10)


def test_optimize_image_bytes_produces_valid_jpeg_under_size_limit():
    img = Image.new("RGB", (200, 200), color="red")
    data = optimize_image_bytes(img, max_size_kb=50)
    assert len(data) > 0
    # round-trips as a real JPEG
    result = Image.open(io.BytesIO(data))
    assert result.format == "JPEG"


def test_optimize_image_bytes_converts_rgba_to_rgb():
    img = Image.new("RGBA", (20, 20), color=(0, 0, 0, 0))
    data = optimize_image_bytes(img)
    result = Image.open(io.BytesIO(data))
    assert result.mode == "RGB"


def test_optimize_image_reads_from_disk(tmp_path):
    path = _make_image(tmp_path / "test.png")
    data = optimize_image(str(path))
    assert len(data) > 0


def test_optimize_image_rejects_decompression_bomb_sized_images(tmp_path, monkeypatch):
    import src.agent_vision as av
    monkeypatch.setattr(av, "MAX_PIXELS", 100)  # tiny limit for the test
    path = _make_image(tmp_path / "big.png", size=(50, 50))
    with pytest.raises(ValueError, match="decompression-bomb"):
        optimize_image(str(path))


def test_validate_extracted_data():
    assert validate_extracted_data({"deceased": {}}) is True
    assert validate_extracted_data({"customer": {}}) is False


def test_prepare_input_files_skips_missing_files():
    assert prepare_input_files(["/no/such/file.jpg"]) == []


def test_prepare_input_files_skips_oversized_files(tmp_path, monkeypatch):
    import src.agent_vision as av
    path = _make_image(tmp_path / "photo.jpg")
    monkeypatch.setattr(av, "MAX_IMAGE_SIZE", 1)  # anything real is "too big"
    assert prepare_input_files([str(path)]) == []


def test_prepare_input_files_includes_valid_image(tmp_path):
    path = _make_image(tmp_path / "photo.jpg")
    parts = prepare_input_files([str(path)])
    assert len(parts) == 1
    assert parts[0]["mime_type"] == "image/jpeg"


def test_extract_raw_data_returns_empty_object_with_no_files():
    assert extract_raw_data([]) == "{}"


@patch("src.agent_vision.get_client")
def test_extract_raw_data_happy_path(mock_get_client, tmp_path):
    path = _make_image(tmp_path / "photo.jpg")

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "deceased": {"fio": "Тест"},
        "services": [{"name": "Дубль", "price": 100}, {"name": "Дубль", "price": 100}],
    })
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    result_str = extract_raw_data([str(path)])
    result = json.loads(result_str)

    assert result["deceased"]["fio"] == "Тест"
    assert len(result["services"]) == 1  # deduplicated
    mock_client.models.generate_content.assert_called_once()


@patch("src.agent_vision.get_client")
def test_extract_raw_data_retries_then_gives_up(mock_get_client, tmp_path):
    path = _make_image(tmp_path / "photo.jpg")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("API down")
    mock_get_client.return_value = mock_client

    with patch("src.agent_vision.time.sleep"):
        result = extract_raw_data([str(path)], retries=2)

    assert result == "{}"
    assert mock_client.models.generate_content.call_count == 2


@patch("src.agent_vision.get_client")
def test_extract_raw_data_rejects_response_without_deceased_block(mock_get_client, tmp_path):
    path = _make_image(tmp_path / "photo.jpg")

    mock_response = MagicMock()
    mock_response.text = json.dumps({"customer": {}})  # missing "deceased"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    with patch("src.agent_vision.time.sleep"):
        result = extract_raw_data([str(path)], retries=1)

    assert result == "{}"


def test_extract_raw_data_fails_fast_when_circuit_breaker_open(tmp_path):
    from src.circuit_breaker import gemini_circuit_breaker
    from src.errors import GeminiUnavailableError

    path = _make_image(tmp_path / "photo.jpg")
    gemini_circuit_breaker.record_failure()  # threshold is 5; force it open directly
    for _ in range(10):
        gemini_circuit_breaker.record_failure()

    with patch("src.agent_vision.get_client") as mock_get_client, pytest.raises(GeminiUnavailableError):
        extract_raw_data([str(path)])

    mock_get_client.assert_not_called()  # never even tries to build a client


@patch("src.agent_vision.get_client")
def test_extract_raw_data_records_success_on_the_shared_breaker(mock_get_client, tmp_path):
    from src.circuit_breaker import gemini_circuit_breaker

    path = _make_image(tmp_path / "photo.jpg")
    gemini_circuit_breaker.record_failure()
    gemini_circuit_breaker.record_failure()

    mock_response = MagicMock()
    mock_response.text = json.dumps({"deceased": {"fio": "Тест"}})
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_get_client.return_value = mock_client

    extract_raw_data([str(path)])

    assert gemini_circuit_breaker.is_open is False
    # success resets the streak - a *new* single failure shouldn't be
    # enough to trip a breaker with the default threshold of 5
    gemini_circuit_breaker.record_failure()
    assert gemini_circuit_breaker.is_open is False
