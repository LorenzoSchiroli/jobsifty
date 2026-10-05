from unittest.mock import patch

from ingestion.backfill.__main__ import _to_offer, run
from ingestion.offer.models import EnrichedOffer


def _item(i: int) -> dict:
    return {
        "id": str(i), "url": f"https://x/{i}", "title": f"Job {i}", "company_name": "Acme",
        "location": None, "source": None, "posted_at": "2024-01-15T00:00:00+00:00", "description": "desc",
    }


def _derived(offer):
    return EnrichedOffer(title=offer.title, company=offer.company, location=offer.location, url=offer.url,
                         source=offer.source, posted_at=offer.posted_at)


def test_to_offer_handles_nulls():
    offer = _to_offer(_item(1))
    assert offer.location == "" and offer.source == ""
    assert offer.posted_at.year == 2024


def test_run_pages_until_next_after_is_none():
    pages = [([_item(1), _item(2)], "2"), ([_item(3)], None)]
    with patch("ingestion.backfill.__main__.list_jobs", side_effect=pages) as mock_list, \
         patch("ingestion.backfill.__main__.derive", side_effect=_derived), \
         patch("ingestion.backfill.__main__.post_job") as mock_post:
        counts = run()
    assert counts == {"seen": 3, "posted": 3, "errors": 0}
    assert mock_list.call_args_list[1].kwargs["after"] == "2"
    assert mock_post.call_count == 3


def test_run_continues_on_error_and_dry_run_skips_post():
    with patch("ingestion.backfill.__main__.list_jobs", return_value=([_item(1), _item(2)], None)), \
         patch("ingestion.backfill.__main__.derive", side_effect=[RuntimeError("bad"), _derived(_to_offer(_item(2)))]), \
         patch("ingestion.backfill.__main__.post_job") as mock_post:
        counts = run(dry_run=True)
    assert counts == {"seen": 2, "posted": 1, "errors": 1}
    mock_post.assert_not_called()


def test_run_respects_limit_pages():
    with patch("ingestion.backfill.__main__.list_jobs", return_value=([_item(1)], "1")) as mock_list, \
         patch("ingestion.backfill.__main__.derive", side_effect=_derived), \
         patch("ingestion.backfill.__main__.post_job"):
        run(limit_pages=2)
    assert mock_list.call_count == 2
