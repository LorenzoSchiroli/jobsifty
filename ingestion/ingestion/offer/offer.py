import logging
import time
from tqdm import tqdm

from ingestion.embedder import embed_chunks
from ingestion.offer.models import EnrichedOffer
from ingestion.offer.parsing.parsing import parse_with_text
from ingestion.offer.scraping.filters import _fetch_description_from_url
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scraping import scrape
from ingestion.utils.text import truncate_description

logger = logging.getLogger(__name__)

def derive(offer: JobOffer) -> EnrichedOffer:
    """Parse fields and embed chunks — no network, no LLM."""
    extraction, body = parse_with_text(offer)
    return EnrichedOffer(
        title=offer.title,
        company=offer.company,
        location=offer.location,
        url=offer.url,
        source=offer.source,
        posted_at=offer.posted_at,
        description=offer.description,
        chunks=embed_chunks(offer.title, body),
        **extraction.model_dump(),
    )


def _enrich_one(offer: JobOffer) -> EnrichedOffer:
    if offer.url and not offer.description:
        logger.info("fetch  [%s] %s", offer.source, offer.title[:60])
        full_desc = _fetch_description_from_url(offer.url)
        if full_desc:
            offer.description = truncate_description(full_desc)

    if offer.description:
        offer.description = truncate_description(offer.description)

    logger.info("parse  [%s] %s", offer.source, offer.title[:60])
    return derive(offer)


def enrich_all(query: str, hours: int, sources=None) -> list[EnrichedOffer]:
    t0 = time.monotonic()
    offers = scrape(query, hours, sources)
    if not offers:
        return []
    logger.info("Enriching %d offers...", len(offers))
    results = []
    for offer in tqdm(offers, desc="Enriching offers", unit="offer"):
        try:
            results.append(_enrich_one(offer))
        except Exception as e:
            logger.warning("Failed to enrich offer: %s", e)
    elapsed = time.monotonic() - t0
    logger.info("Enrichment completed in %.1fs — %d offers enriched.", elapsed, len(results))
    return results
