from unittest.mock import MagicMock, patch
import pytest
import requests
from ingestion.offer.models import EnrichedOffer
from ingestion.client import get_pending_companies, list_jobs, post_job, post_company


@pytest.fixture(autouse=True)
def backend_url(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "http://localhost:8000")


def _resp(status: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.ok = status < 400
    r.json.return_value = body
    r.text = str(body)
    return r


def _offer(**kwargs) -> EnrichedOffer:
    defaults = dict(
        title="ML Engineer",
        company="Acme Corp",
        location="Berlin",
        url="https://example.com/job/1",
        source="jobspy",
        posted_at=None,
    )
    return EnrichedOffer(**{**defaults, **kwargs})


def test_post_job_renames_company_to_company_name():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(201, {"id": "abc"})
        post_job(_offer())

    payload = mock_post.call_args.kwargs["json"]
    assert payload["company_name"] == "Acme Corp"
    assert "company" not in payload


def test_post_job_returns_status_and_body():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(200, {"id": "abc", "title": "ML Engineer"})
        status, body = post_job(_offer())

    assert status == 200
    assert body["id"] == "abc"


def test_post_job_hits_correct_url():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(201, {})
        post_job(_offer())

    assert mock_post.call_args.args[0] == "http://localhost:8000/jobs/"


def test_post_company_sends_dict_and_returns_status():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(201, {"id": "xyz", "name": "Acme"})
        status, body = post_company({"name": "Acme"})

    assert status == 201
    assert mock_post.call_args.kwargs["json"] == {"name": "Acme"}


def test_post_company_hits_correct_url():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(200, {})
        post_company({"name": "Acme"})

    assert mock_post.call_args.args[0] == "http://localhost:8000/companies/"


def test_post_job_raises_on_http_error():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(422, {"detail": "validation error"})
        with pytest.raises(requests.HTTPError):
            post_job(_offer())


def test_post_company_raises_on_http_error():
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(500, {"detail": "internal error"})
        with pytest.raises(requests.HTTPError):
            post_company({"name": "Acme"})


def test_post_job_sends_chunks():
    chunks = [{"text": "ML Engineer\nBuild models", "embedding": [0.1, 0.2]}]
    with patch("ingestion.client.requests.post") as mock_post:
        mock_post.return_value = _resp(201, {"id": "abc"})
        post_job(_offer(chunks=chunks))
    payload = mock_post.call_args.kwargs["json"]
    assert payload["chunks"] == chunks
    assert "embedding" not in payload


def test_list_jobs_passes_keyset_params():
    with patch("ingestion.client.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"items": [{"id": "1"}], "next_after": "1"})
        items, next_after = list_jobs(after="0", limit=10)
    assert mock_get.call_args.args[0] == "http://localhost:8000/jobs/"
    assert mock_get.call_args.kwargs["params"] == {"limit": 10, "after": "0"}
    assert items == [{"id": "1"}] and next_after == "1"


def test_list_jobs_first_page_has_no_after():
    with patch("ingestion.client.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"items": [], "next_after": None})
        items, next_after = list_jobs()
    assert "after" not in mock_get.call_args.kwargs["params"]
    assert items == [] and next_after is None


def test_post_job_does_not_send_company_info():
    with patch("ingestion.client.requests.post", return_value=_resp(201, {})) as mock_post:
        post_job(_offer(company_info={"website": "https://acme.com"}))
    assert "company_info" not in mock_post.call_args.kwargs["json"]


def test_get_pending_companies_passes_limit():
    rows = [{"name": "acme", "location": "Berlin", "live_jobs": 3}]
    with patch("ingestion.client.requests.get", return_value=_resp(200, rows)) as mock_get:
        assert get_pending_companies(2) == rows
    assert mock_get.call_args.args[0] == "http://localhost:8000/companies/pending"
    assert mock_get.call_args.kwargs["params"] == {"limit": 2}
