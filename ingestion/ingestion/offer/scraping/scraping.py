import logging
from datetime import datetime

from tqdm import tqdm

from ingestion.offer.scraping.deduplicator import deduplicate
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scopes import Scope
from ingestion.offer.scraping.sources.adzuna_source import AdzunaSource
from ingestion.offer.scraping.sources.arbeitnow_source import ArbeitnowSource
from ingestion.offer.scraping.sources.base import Source
from ingestion.offer.scraping.sources.freehire_source import FreehireSource
from ingestion.offer.scraping.sources.jobspy_source import JobspySource

logger = logging.getLogger(__name__)

_ALL_SOURCE_CLASSES = [AdzunaSource, ArbeitnowSource, FreehireSource, JobspySource]


def scrape(
    scope: Scope, hours: int, sources: list[Source] | None = None, known_urls: frozenset[str] = frozenset(),
) -> list[JobOffer]:
    """Offers for `scope` from every source, without off-scope titles, jobs already stored
    (`known_urls`) and duplicates."""
    if sources is None:
        sources = [cls() for cls in _ALL_SOURCE_CLASSES]
    logger.info("Scraping %d source(s) for scope %r...", len(sources), scope.name)
    all_offers: list[JobOffer] = []
    for src in tqdm(sources, desc="Scraping sources", unit="source"):
        # Sources are meant to return [] on failure; one that raises anyway (a
        # changed API, a parsing bug) must not cost the run the other sources.
        try:
            offers = src.fetch(scope, hours, known_urls)
        except Exception:
            logger.exception("  %-20s failed; skipped", src.__class__.__name__)
            continue
        in_scope = [o for o in offers if scope.matches(o.title)]
        new = [o for o in in_scope if o.url not in known_urls]
        logger.info("  %-20s %d offers: %d off-scope, %d already stored, %d kept", src.__class__.__name__,
                    len(offers), len(offers) - len(in_scope), len(in_scope) - len(new), len(new))
        all_offers.extend(new)
    # A job without an employer name cannot be tied to a company (jobspy used to
    # turn a missing name into a company literally called "nan").
    named = [o for o in all_offers if o.company.strip()]
    if dropped := len(all_offers) - len(named):
        logger.info("Dropped %d offers without a company name", dropped)
    deduped = deduplicate(named)
    deduped.sort(key=lambda o: o.posted_at or datetime.min, reverse=True)
    logger.info("Scraped %d offers total (after dedup)", len(deduped))
    return deduped
