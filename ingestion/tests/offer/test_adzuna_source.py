from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from ingestion.offer.scraping.sources.adzuna_source import AdzunaSource
from ingestion.offer.scraping.scopes import AI

_recent = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")

MOCK_RESPONSE = {
    "results": [
        {
            "title": "Machine Learning Engineer",
            "company": {"display_name": "DataCo"},
            "location": {"display_name": "London, UK"},
            "redirect_url": "https://adzuna.co.uk/jobs/details/123",
            "created": _recent,
            "description": "Develop ML models and pipelines.",
        }
    ]
}


def _mock_get(response):
    mock = MagicMock()
    mock.json.return_value = response
    mock.raise_for_status = MagicMock()
    return mock


def test_returns_offers_with_valid_keys():
    with patch.dict("os.environ", {"ADZUNA_APP_ID": "fake_id", "ADZUNA_APP_KEY": "fake_key"}):
        with patch("ingestion.offer.scraping.sources.adzuna_source.requests.get", return_value=_mock_get(MOCK_RESPONSE)):
            results = AdzunaSource().fetch(AI, hours=72)

    assert len(results) > 0
    assert results[0].title == "Machine Learning Engineer"
    assert results[0].source == "adzuna"


def test_returns_empty_when_keys_missing():
    with patch.dict("os.environ", {}, clear=True):
        results = AdzunaSource().fetch(AI, hours=72)
    assert results == []


def test_returns_empty_on_http_error():
    with patch.dict("os.environ", {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"}):
        with patch("ingestion.offer.scraping.sources.adzuna_source.requests.get", side_effect=Exception("403")):
            results = AdzunaSource().fetch(AI, hours=72)
    assert results == []


def test_description_left_for_url_fetch():
    """Adzuna only returns a truncated snippet; enrich_all fetches the full page."""
    with patch.dict("os.environ", {"ADZUNA_APP_ID": "fake_id", "ADZUNA_APP_KEY": "fake_key"}):
        with patch("ingestion.offer.scraping.sources.adzuna_source.requests.get", return_value=_mock_get(MOCK_RESPONSE)):
            results = AdzunaSource().fetch(AI, hours=72)

    assert results[0].description is None


def test_http_error_log_redacts_app_key(caplog):
    err = Exception("503 Server Error for url: https://api.adzuna.com/...?app_id=id&app_key=s3cret")
    with patch.dict("os.environ", {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "s3cret"}):
        with patch("ingestion.offer.scraping.sources.adzuna_source.requests.get", side_effect=err):
            AdzunaSource().fetch(AI, hours=72)
    assert "s3cret" not in caplog.text
    assert "app_key=***" in caplog.text


def test_asks_for_the_scope_terms_newest_first_and_cuts_the_hours_itself():
    from datetime import timezone
    now = datetime.now(timezone.utc)
    stamp = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    response = {"results": [
        {**MOCK_RESPONSE["results"][0], "redirect_url": "https://a/1", "created": stamp(1)},
        {**MOCK_RESPONSE["results"][0], "redirect_url": "https://a/2", "created": stamp(6)},  # inside Adzuna's day
    ]}
    with patch.dict("os.environ", {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"}):
        with patch("ingestion.offer.scraping.sources.adzuna_source.requests.get",
                   return_value=_mock_get(response)) as get:
            results = AdzunaSource().fetch(AI, hours=4)
    params = get.call_args.kwargs["params"]
    assert params["what_or"] == " ".join(AI.terms)
    assert params["sort_by"] == "date" and params["max_days_old"] == 1
    assert {r.url for r in results} == {"https://a/1"}
