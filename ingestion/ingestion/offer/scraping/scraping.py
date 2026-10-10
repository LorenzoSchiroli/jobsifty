import logging
from datetime import datetime

from tqdm import tqdm

from ingestion.offer.scraping.deduplicator import deduplicate
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.sources.adzuna_source import AdzunaSource
from ingestion.offer.scraping.sources.arbeitnow_source import ArbeitnowSource
from ingestion.offer.scraping.sources.base import Source
from ingestion.offer.scraping.sources.jobspy_source import JobspySource
from ingestion.offer.scraping.sources.remotive_source import RemotiveSource

logger = logging.getLogger(__name__)

_ALL_SOURCE_CLASSES = [AdzunaSource, ArbeitnowSource, JobspySource, RemotiveSource]


def scrape(query: str, hours: int, sources: list[Source] | None = None) -> list[JobOffer]:
    if sources is None:
        sources = [cls() for cls in _ALL_SOURCE_CLASSES]
    logger.info("Scraping %d source(s)...", len(sources))
    all_offers: list[JobOffer] = []
    for src in tqdm(sources, desc="Scraping sources", unit="source"):
        offers = src.fetch(query, hours)
        logger.info("  %-20s %d offers", src.__class__.__name__, len(offers))
        all_offers.extend(offers)
    # A job without an employer name cannot be tied to a company (jobspy used to
    # turn a missing name into a company literally called "nan").
    named = [o for o in all_offers if o.company.strip()]
    if dropped := len(all_offers) - len(named):
        logger.info("Dropped %d offers without a company name", dropped)
    deduped = deduplicate(named)
    deduped.sort(key=lambda o: o.posted_at or datetime.min, reverse=True)
    logger.info("Scraped %d offers total (after dedup)", len(deduped))
    return deduped
