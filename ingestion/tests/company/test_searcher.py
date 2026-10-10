from unittest.mock import MagicMock, patch

import pytest

from ingestion.company.scraping import searcher
from ingestion.company.scraping.searcher import (
    breaker_open, location_hint, normalize_linkedin_url, pick_website, search_company, searches_used,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # only Tavily configured: the pool's order and fallbacks are covered in test_providers
    for key in ("TINYFISH_API_KEY", "EXA_API_KEY", "SEARCH_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    searcher.reset()
    yield
    searcher.reset()


def _response(status: int = 200, body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(status_code=status, text=text)
    resp.json.return_value = body or {}
    return resp


@pytest.mark.parametrize("location, expected", [
    ("Paris, Île-de-France, France", "Paris France"),
    ("Berlin", "Berlin"),
    ("Werk van thuis, BE", "BE"),
    ("Remote", ""),
    (None, ""),
])
def test_location_hint(location, expected):
    assert location_hint(location) == expected


@pytest.mark.parametrize("name, urls, expected", [
    # data aggregators and directories are never the employer's site
    ("Neara", ["https://leadiq.com/c/neara/5dad", "https://rocketreach.co/neara"], None),
    ("Data Wizards", ["https://leadiq.com/c/x", "https://datawizards.io/about"], "https://datawizards.io"),
    # the whole name beats a shared first word
    ("Siemens Healthineers", ["https://www.siemens.com/x", "https://www.siemens-healthineers.com/careers"],
     "https://www.siemens-healthineers.com"),
    ("Accenture Greece", ["https://tracxn.com/d/x", "https://www.accenture.com/gr-en"], "https://www.accenture.com"),
    ("Reddit, Inc.", ["https://www.redditinc.com/"], "https://www.redditinc.com"),
    ("Hadrian", ["https://research.contrary.com/company/hadrian"], None),
    ("X4 Technology", ["https://www.x4-technology.com/us"], "https://www.x4-technology.com"),
])
def test_pick_website(name, urls, expected):
    assert pick_website(name, urls) == expected


def test_normalize_linkedin_url():
    assert normalize_linkedin_url("https://ee.linkedin.com/company/markit-eu?trk=x") == "https://www.linkedin.com/company/markit-eu"
    assert normalize_linkedin_url("https://www.linkedin.com/in/someone") is None


def test_search_company_combines_both_searches():
    company = {
        "answer": "Flyability, a Swiss company founded in 2014...",
        "results": [
            {"url": "https://www.linkedin.com/company/flyability", "content": "Flyability | LinkedIn"},
            {"url": "https://www.flyability.com/about", "content": "Collision-tolerant drones."},
        ],
    }
    reviews = {"results": [{"url": "https://www.glassdoor.com/Overview/flyability", "content": "Rated 3.8 out of 5"}]}

    def post(url, headers, json, timeout):
        assert headers["Authorization"] == "Bearer test-key"
        return _response(body=reviews if "glassdoor" in json["query"] else company)

    with patch("ingestion.company.scraping.providers.requests.post", side_effect=post) as mock_post:
        result = search_company("Flyability", "Paudex, Vaud, Switzerland")

    queries = sorted(c.kwargs["json"]["query"] for c in mock_post.call_args_list)
    assert queries == ["Flyability Paudex Switzerland company", "Flyability glassdoor reviews"]
    assert result.website == "https://www.flyability.com"
    assert result.linkedin_url == "https://www.linkedin.com/company/flyability"
    assert result.answer.startswith("Flyability")
    assert result.review_snippets == ["Rated 3.8 out of 5"]
    assert searches_used() == 2


def test_quota_error_stops_searching_for_the_run():
    with patch("ingestion.company.scraping.providers.requests.post",
               return_value=_response(432, text='{"detail": "usage limit exceeded"}')) as mock_post:
        first = search_company("Acme", "Berlin")
        calls_for_first = mock_post.call_count  # 1 or 2: the two searches run in parallel
        second = search_company("Other", "Berlin")

    assert breaker_open()
    assert mock_post.call_count == calls_for_first  # nothing for the second company
    assert first.answer is None and second.website is None


def test_transient_errors_do_not_stop_searching():
    with patch("ingestion.company.scraping.providers.requests.post", return_value=_response(500, text="oops")):
        search_company("Acme", "Berlin")
    assert not breaker_open()


def test_no_api_key_means_no_search(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    with patch("ingestion.company.scraping.providers.requests.post") as mock_post:
        result = search_company("Acme", "Berlin")
    mock_post.assert_not_called()
    assert result.answer is None and searches_used() == 0
