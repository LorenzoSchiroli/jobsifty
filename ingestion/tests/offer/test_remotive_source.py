from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from ingestion.offer.scraping.sources.remotive_source import RemotiveSource

_recent = (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
_old = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

MOCK_RESPONSE = {
    "jobs": [
        {
            "title": "Senior Python Developer",
            "company_name": "Remote Inc",
            "url": "https://remotive.com/remote-jobs/python-dev-123",
            "candidate_required_location": "Europe",
            "publication_date": _recent,
            "description": "<p>We need a <b>senior developer</b> with Python skills.</p>",
        },
        {
            "title": "Développeur Python",
            "company_name": "FrenchCo",
            "url": "https://remotive.com/remote-jobs/dev-456",
            "candidate_required_location": "France",
            "publication_date": _recent,
            "description": "<p>Description en français.</p>",
        },
        {
            "title": "Python Engineer",
            "company_name": "OldRemote",
            "url": "https://remotive.com/remote-jobs/py-789",
            "candidate_required_location": "Worldwide",
            "publication_date": _old,
            "description": "<p>Old job.</p>",
        },
    ]
}


def _mock_get(response):
    mock = MagicMock()
    mock.json.return_value = response
    mock.raise_for_status = MagicMock()
    return mock


def test_returns_matching_english_offers_within_hours():
    with patch("ingestion.offer.scraping.sources.remotive_source.requests.get", return_value=_mock_get(MOCK_RESPONSE)):
        results = RemotiveSource().fetch("python", hours=72)

    assert len(results) == 1
    assert results[0].title == "Senior Python Developer"
    assert results[0].source == "remotive"


def test_description_is_stripped_of_html():
    with patch("ingestion.offer.scraping.sources.remotive_source.requests.get", return_value=_mock_get(MOCK_RESPONSE)):
        results = RemotiveSource().fetch("python", hours=72)

    assert results[0].description is not None
    assert "senior developer" in results[0].description
    assert "<" not in results[0].description


def test_returns_empty_on_error():
    with patch("ingestion.offer.scraping.sources.remotive_source.requests.get", side_effect=Exception("timeout")):
        assert RemotiveSource().fetch("python", hours=72) == []
