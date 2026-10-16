from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

from ingestion.offer.scraping.sources.freehire_source import FreehireSource

_GET = "ingestion.offer.scraping.sources.freehire_source.requests.get"


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(i=1, title="Machine Learning Engineer", indexed_hours_ago=1, posted_hours_ago=None, reality="fresh", **extra):
    return {
        "title": title, "company": "Acme", "location": "Berlin, Germany",
        "url": f"https://boards.greenhouse.io/acme/jobs/{i}?utm_source=freehire.me&gh_src=x",
        "source": "greenhouse", "created_at": _iso(indexed_hours_ago),
        "posted_at": _iso(indexed_hours_ago if posted_hours_ago is None else posted_hours_ago),
        "description": "<h2>Requirements</h2><p>Python and PyTorch.</p>",
        "reality": {"class": reality}, **extra,
    }


def _resp(items):
    resp = MagicMock()
    resp.json.return_value = {"data": items, "meta": {"total": len(items)}}
    resp.raise_for_status.return_value = None
    return resp


@patch("ingestion.offer.scraping.sources.freehire_source.time.sleep")
def test_maps_a_posting(_sleep):
    with patch(_GET, return_value=_resp([_job()])) as get:
        offers = FreehireSource().fetch("machine learning engineer", 2)
    assert len(offers) == 1
    o = offers[0]
    assert (o.title, o.company, o.location) == ("Machine Learning Engineer", "Acme", "Berlin, Germany")
    assert o.source == "freehire:greenhouse"
    assert o.url == "https://boards.greenhouse.io/acme/jobs/1?gh_src=x"  # utm stripped, the rest kept
    assert "Python and PyTorch" in o.description
    params = get.call_args.kwargs["params"]
    assert params["regions"] == "eu" and params["sort"] == "created_at" and params["offset"] == 0


@patch("ingestion.offer.scraping.sources.freehire_source.time.sleep")
def test_skips_stale_evergreen_closed_old_and_non_english(_sleep):
    items = [
        _job(1, posted_hours_ago=10),               # indexed late: inside the window by created_at
        _job(2, posted_hours_ago=24 * 40),          # posted beyond the 30-day retention
        _job(6, closed_at=_iso(0)),
        _job(3, reality="stale"),                   # re-posted under a fresh date
        _job(4, reality="likely-evergreen"),
        _job(5, title="Ingeniero de aprendizaje automático"),
        {"title": "No url", "created_at": _iso(1), "posted_at": _iso(1)},
        "not a dict",
    ]
    with patch(_GET, return_value=_resp(items)):
        offers = FreehireSource().fetch("q", 2)
    assert [o.url.rsplit("/", 1)[-1].split("?")[0] for o in offers] == ["1"]


@patch("ingestion.offer.scraping.sources.freehire_source.time.sleep")
def test_pages_until_a_short_page(_sleep):
    full = [_job(i) for i in range(50)]
    with patch(_GET, side_effect=[_resp(full), _resp([_job(99)])]) as get:
        offers = FreehireSource().fetch("q", 2)
    assert len(offers) == 51
    assert [c.kwargs["params"]["offset"] for c in get.call_args_list] == [0, 50]


@patch("ingestion.offer.scraping.sources.freehire_source.time.sleep")
def test_stops_at_the_first_posting_indexed_before_the_window(_sleep):
    page = [_job(1, indexed_hours_ago=0.5), _job(2, indexed_hours_ago=1.5)] + [_job(i, indexed_hours_ago=3) for i in range(3, 51)]
    with patch(_GET, side_effect=[_resp(page)]) as get:
        offers = FreehireSource().fetch("q", 2)
    assert len(offers) == 2
    assert get.call_count == 1


@patch("ingestion.offer.scraping.sources.freehire_source.time.sleep")
def test_a_failing_page_keeps_the_earlier_ones(_sleep):
    full = [_job(i) for i in range(50)]
    with patch(_GET, side_effect=[_resp(full), requests.ConnectionError("down")]):
        offers = FreehireSource().fetch("q", 2)
    assert len(offers) == 50


def test_api_down_returns_empty():
    with patch(_GET, side_effect=requests.ConnectionError("down")):
        assert FreehireSource().fetch("q", 2) == []
