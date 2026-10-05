from unittest.mock import MagicMock, patch

from ingestion.offer.scraping.filters import is_english, _strip_html, _fetch_description_from_url


def test_english_title_passes():
    assert is_english("Machine Learning Engineer") is True


def test_german_title_fails():
    assert is_english("Softwareentwickler für maschinelles Lernen") is False


def test_french_title_fails():
    assert is_english("Ingénieur en apprentissage automatique") is False


def test_empty_string_passes():
    assert is_english("") is True


def test_mixed_mostly_ascii_passes():
    assert is_english("Senior Engineer — Berlin") is True


def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_removes_script_content():
    result = _strip_html("<script>console.log('x')</script><p>visible</p>")
    assert "console.log" not in result
    assert "visible" in result


def test_strip_html_removes_style_content():
    result = _strip_html("<style>.foo { color: red }</style><p>text</p>")
    assert "color" not in result
    assert "text" in result


def test_strip_html_returns_plain_text_without_angle_brackets():
    html = "<div><h1>Job Title</h1><p>We need a developer.</p></div>"
    result = _strip_html(html)
    assert "Job Title" in result
    assert "developer" in result
    assert "<" not in result


def test_strip_html_handles_empty_string():
    assert _strip_html("") == ""


def test_strip_html_handles_plain_text_passthrough():
    assert _strip_html("plain text") == "plain text"


def test_fetch_description_from_url_returns_stripped_text():
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><p>Full job description here.</p></body></html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("ingestion.offer.scraping.filters.requests.get", return_value=mock_resp):
        result = _fetch_description_from_url("https://example.com/job/1")

    assert result is not None
    assert "Full job description here" in result
    assert "<" not in result


def test_fetch_description_from_url_returns_none_on_error():
    with patch("ingestion.offer.scraping.filters.requests.get", side_effect=Exception("timeout")):
        result = _fetch_description_from_url("https://example.com/job/1")

    assert result is None


def test_fetch_description_from_url_returns_none_on_empty_page():
    mock_resp = MagicMock()
    mock_resp.text = "<html><body></body></html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("ingestion.offer.scraping.filters.requests.get", return_value=mock_resp):
        result = _fetch_description_from_url("https://example.com/job/1")

    assert result is None
