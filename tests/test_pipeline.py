import json
from unittest.mock import patch

import pytest

from src import pipeline
from src.errors import GeminiParsingError, ValidationFailedError


def _valid_final_json():
    return {
        "deceased": {"fio": "Тест Тестович"},
        "customer": {"fio": "Заказчик"},
        "services": [{"name": "Катафалк", "price": 2000, "quantity": 1}],
        "goods": [],
        "transport": [],
        "handwritten_total": 2000,
    }


def test_run_pipeline_normalizes_dict_booked_items():
    """Regression: capture_booked_items_screenshot() returns list[dict], but
    apply_business_rules_in_python expects list[str]. Passing dicts straight
    through used to crash with AttributeError on every duplicate-scan run."""
    captured = {}

    def fake_parse(paths, num_addresses, booked_in_1c):
        captured["booked_in_1c"] = booked_in_1c
        assert all(isinstance(x, str) for x in booked_in_1c)
        return "{}", _valid_final_json()

    with patch("src.pipeline.parse_images_with_gemini", side_effect=fake_parse):
        result = pipeline.run_pipeline(
            ["a.jpg"], num_addresses=0,
            booked_in_1c=[{"name": "Катафалк", "quantity": 1, "price": 2000, "sum": 2000}],
        )

    assert captured["booked_in_1c"] == ["Катафалк"]
    assert result.order_data["deceased"]["fio"] == "Тест Тестович"
    assert result.summary.deceased_fio == "Тест Тестович"


def test_run_pipeline_raises_on_empty_gemini_result():
    with patch("src.pipeline.parse_images_with_gemini", return_value=("", {})), pytest.raises(GeminiParsingError):
        pipeline.run_pipeline(["a.jpg"], num_addresses=0)


def test_run_pipeline_raises_on_validation_failure():
    with patch("src.pipeline.parse_images_with_gemini", return_value=("{}", {"garbage": True})), \
         patch("src.pipeline.validate_and_fix_order", return_value={}), pytest.raises(ValidationFailedError):
        pipeline.run_pipeline(["a.jpg"], num_addresses=0)


def test_write_audit_log(tmp_path):
    with patch("src.pipeline.get_settings") as mock_settings:
        mock_settings.return_value.output_dir = tmp_path
        result = pipeline.PipelineResult(
            raw_json_str=json.dumps({"raw": True}),
            order_data={"final": True},
            summary=None,
            elapsed_seconds=1.0,
        )
        log_path = pipeline.write_audit_log(result, order_id="ORD-1")

    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["order_id"] == "ORD-1"
    assert payload["raw"] == {"raw": True}
    assert payload["final"] == {"final": True}
