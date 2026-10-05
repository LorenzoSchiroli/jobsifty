from unittest.mock import MagicMock, patch

from ingestion.company.scraping.fetcher import fetch_html


def test_fetch_returns_html_on_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body>Hello</body></html>"

    with patch("ingestion.company.scraping.fetcher.requests.get", return_value=mock_resp):
        result = fetch_html("https://example.com")

    assert result == "<html><body>Hello</body></html>"


def test_fetch_falls_back_to_playwright_on_403():
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("ingestion.company.scraping.fetcher.requests.get", return_value=mock_resp):
        with patch("ingestion.company.scraping.fetcher._fetch_with_playwright", return_value="<html>pw</html>"):
            result = fetch_html("https://example.com")

    assert result == "<html>pw</html>"


def test_fetch_falls_back_to_playwright_on_exception():
    with patch("ingestion.company.scraping.fetcher.requests.get", side_effect=Exception("timeout")):
        with patch("ingestion.company.scraping.fetcher._fetch_with_playwright", return_value="<html>pw</html>"):
            result = fetch_html("https://example.com")

    assert result == "<html>pw</html>"


def test_fetch_returns_none_when_both_fail():
    with patch("ingestion.company.scraping.fetcher.requests.get", side_effect=Exception("timeout")):
        with patch("ingestion.company.scraping.fetcher._fetch_with_playwright", return_value=None):
            result = fetch_html("https://example.com")

    assert result is None
