from datetime import date
from unittest.mock import patch

import pytest

from ingestion.offer.models import EnrichedOffer, OfferExtraction
from ingestion.offer.offer import derive, enrich_all
from ingestion.offer.scraping.models import JobOffer

_CHUNKS = [{"text": "t", "embedding": [0.0] * 384}]


def _make_offer(title: str, description: str | None = "Short snippet.", source: str = "test") -> JobOffer:
    return JobOffer(
        title=title,
        company="Acme",
        location="Berlin",
        url=f"https://example.com/{title}",
        source=source,
        posted_at=date(2024, 1, 15),
        description=description,
    )


@pytest.fixture(autouse=True)
def fake_embedder():
    with patch("ingestion.offer.offer.embed_chunks", return_value=_CHUNKS) as m:
        yield m


def _patch_fetch(return_value=None):
    return patch("ingestion.offer.offer._fetch_description_from_url", return_value=return_value)


def test_enrich_all_returns_enriched_offers():
    offers = [_make_offer("Python Engineer"), _make_offer("Data Scientist")]
    extraction = OfferExtraction(employment_type="full-time", seniority="senior")

    with patch("ingestion.offer.offer.scrape", return_value=offers):
        with patch("ingestion.offer.offer.parse_with_text", return_value=(extraction, "body")):
            results = enrich_all("python", hours=72)

    assert len(results) == 2
    assert all(isinstance(r, EnrichedOffer) for r in results)
    assert results[0].employment_type == "full-time"
    assert results[0].chunks == _CHUNKS


def test_enrich_all_returns_empty_list_when_no_offers():
    with patch("ingestion.offer.offer.scrape", return_value=[]):
        assert enrich_all("python", hours=72) == []


def test_enrich_all_skips_offer_that_fails():
    offers = [_make_offer("A"), _make_offer("B")]
    with patch("ingestion.offer.offer.scrape", return_value=offers):
        with patch("ingestion.offer.offer.parse_with_text", side_effect=[RuntimeError("boom"), (OfferExtraction(), "")]):
            results = enrich_all("python", hours=72)
    assert [r.title for r in results] == ["B"]


def test_enrich_all_fetches_description_when_missing():
    offer = _make_offer("Python Dev", description=None)
    with patch("ingestion.offer.offer.scrape", return_value=[offer]):
        with _patch_fetch("Fetched full description.") as mock_fetch:
            results = enrich_all("python", hours=72)
    mock_fetch.assert_called_once_with(offer.url)
    assert results[0].description == "Fetched full description."


def test_enrich_all_skips_fetch_when_description_present():
    offer = _make_offer("Python Dev", description="x" * 600)
    with patch("ingestion.offer.offer.scrape", return_value=[offer]):
        with _patch_fetch() as mock_fetch:
            enrich_all("python", hours=72)
    mock_fetch.assert_not_called()


def test_derive_embeds_kept_text(fake_embedder):
    offer = _make_offer("Senior ML Engineer", description="About us\nWe are Acme.\n\nResponsibilities\n- Train models in Python")
    result = derive(offer)
    assert result.seniority == "senior"
    title, text = fake_embedder.call_args.args
    assert title == "Senior ML Engineer"
    assert "Train models" in text and "We are Acme" not in text
