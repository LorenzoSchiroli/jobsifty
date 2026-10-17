from abc import ABC, abstractmethod
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scopes import Scope


class Source(ABC):
    @abstractmethod
    def fetch(self, scope: Scope, hours: int, known_urls: frozenset[str] = frozenset()) -> list[JobOffer]:
        """Offers for `scope` posted (or, for an aggregator, found) within the last `hours`
        hours. `scrape()` drops off-scope titles and `known_urls` afterwards; a source only
        uses them to save work it would otherwise do per job. Never raises: [] on failure."""
