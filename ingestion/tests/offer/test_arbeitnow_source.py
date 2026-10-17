import time
from unittest.mock import MagicMock, patch
from ingestion.offer.scraping.sources.arbeitnow_source import ArbeitnowSource
from ingestion.offer.scraping.scopes import AI

_NOW = time.time()

MOCK_RESPONSE = {
    "data": [
        {
            "title": "Python Developer",
            "company_name": "TechCorp",
            "location": "Berlin, Germany",
            "url": "https://arbeitnow.com/jobs/python-dev-123",
            "created_at": int(_NOW - 3600),  # 1 hour ago — within 3 days
            "description": "<p>Build <b>Python</b> services for data pipelines.</p>",
        },
        {
            "title": "Softwareentwickler",  # off-scope: kept here, dropped by scrape()
            "company_name": "GmbH AG",
            "location": "Munich",
            "url": "https://arbeitnow.com/jobs/sw-456",
            "created_at": int(_NOW - 3600),
        },
        {
            "title": "Data Engineer",
            "company_name": "OldCorp",
            "location": "Amsterdam",
            "url": "https://arbeitnow.com/jobs/de-789",
            "created_at": int(_NOW - 7 * 24 * 3600),  # 7 days ago — too old
        },
    ]
}


def _mock_get(response):
    mock = MagicMock()
    mock.json.return_value = response
    mock.raise_for_status = MagicMock()
    return mock


_GET = "ingestion.offer.scraping.sources.arbeitnow_source.requests.get"
_EMPTY = {"data": []}


@patch("ingestion.offer.scraping.sources.arbeitnow_source.time.sleep")
def test_returns_english_offers_within_the_window(_sleep):
    with patch(_GET, side_effect=[_mock_get(MOCK_RESPONSE), _mock_get(_EMPTY)]):
        results = ArbeitnowSource().fetch(AI, hours=72)

    # titles are not matched here: scrape() applies the scope to every source
    assert [r.title for r in results] == ["Python Developer", "Softwareentwickler"]
    assert results[0].source == "arbeitnow"
    assert results[0].company == "TechCorp"
    # UTC, whatever the machine's time zone
    from datetime import datetime, timezone
    assert results[0].posted_at == datetime.fromtimestamp(int(_NOW - 3600), timezone.utc).replace(tzinfo=None)


@patch("ingestion.offer.scraping.sources.arbeitnow_source.time.sleep")
def test_reads_several_pages_and_keeps_them_when_a_later_one_fails(_sleep):
    page2 = {"data": [{**MOCK_RESPONSE["data"][0], "title": "ML Engineer", "url": "https://arbeitnow.com/jobs/ml"}]}
    with patch(_GET, side_effect=[_mock_get(MOCK_RESPONSE), _mock_get(page2), Exception("429")]) as get:
        results = ArbeitnowSource().fetch(AI, hours=72)
    assert [r.title for r in results] == ["Python Developer", "Softwareentwickler", "ML Engineer"]
    assert [c.kwargs["params"]["page"] for c in get.call_args_list] == [1, 2, 3]


@patch("ingestion.offer.scraping.sources.arbeitnow_source.time.sleep")
def test_description_is_stripped_of_html(_sleep):
    with patch(_GET, side_effect=[_mock_get(MOCK_RESPONSE), _mock_get(_EMPTY)]):
        results = ArbeitnowSource().fetch(AI, hours=72)

    assert results[0].description is not None
    assert "Build" in results[0].description
    assert "Python" in results[0].description
    assert "<" not in results[0].description


def test_returns_empty_on_network_error():
    with patch(_GET, side_effect=Exception("timeout")):
        assert ArbeitnowSource().fetch(AI, hours=72) == []
