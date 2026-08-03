from unittest.mock import patch

from src.ai_parser import parse_images_with_gemini


def test_parse_images_returns_empty_when_vision_agent_fails():
    with patch("src.ai_parser.extract_raw_data", return_value="{}"):
        raw, final = parse_images_with_gemini(["a.jpg"], num_addresses=0, booked_in_1c=[])
    assert raw == ""
    assert final == {}


def test_parse_images_returns_empty_when_logic_agent_fails():
    with patch("src.ai_parser.extract_raw_data", return_value='{"deceased": {}}'), \
         patch("src.ai_parser.validate_and_normalize", return_value={}):
        raw, final = parse_images_with_gemini(["a.jpg"], num_addresses=1, booked_in_1c=["Катафалк"])
    assert raw == '{"deceased": {}}'
    assert final == {}


def test_parse_images_happy_path():
    with patch("src.ai_parser.extract_raw_data", return_value='{"deceased": {}}') as mock_extract, \
         patch("src.ai_parser.validate_and_normalize", return_value={"services": []}) as mock_normalize:
        raw, final = parse_images_with_gemini(["a.jpg", "b.jpg"], num_addresses=2, booked_in_1c=["X"])

    mock_extract.assert_called_once_with(["a.jpg", "b.jpg"])
    mock_normalize.assert_called_once_with('{"deceased": {}}', 2, ["X"])
    assert raw == '{"deceased": {}}'
    assert final == {"services": []}
