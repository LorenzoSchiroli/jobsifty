import logging
import time
import requests
from datetime import datetime, timezone
from ingestion.offer.scraping.filters import is_latin_script, _strip_html
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scopes import Scope
from ingestion.offer.scraping.sources.base import Source

logger = logging.getLogger(__name__)

_API_URL = "https://www.arbeitnow.com/api/job-board-api"
_HEADERS = {"User-Agent": "jobsifty-ingestion"}
# The API has no search; the scope's title rule (in scrape()) picks the jobs. Its pages are
# not strictly newest first (page 2 still has jobs a few hours old), so a few are read,
# slowly: it sits behind Cloudflare, which answers a burst with 429s.
_PAGES = 3
_PAGE_PAUSE = 3


class ArbeitnowSource(Source):
    def fetch(self, scope: Scope, hours: int, known_urls: frozenset[str] = frozenset()) -> list[JobOffer]:
        cutoff = time.time() - hours * 3600
        results = []
        for page in range(1, _PAGES + 1):
            if page > 1:
                time.sleep(_PAGE_PAUSE)
            try:
                resp = requests.get(_API_URL, params={"page": page}, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json().get("data", [])
            except Exception as e:
                # keep what earlier pages returned
                logger.warning("Arbeitnow fetch failed (page %d): %s", page, e)
                break
            for item in data:
                created = item.get("created_at") or 0
                title = item.get("title", "")
                if created < cutoff or not is_latin_script(title):
                    continue
                results.append(JobOffer(
                    title=title,
                    company=item.get("company_name", ""),
                    location=item.get("location", ""),
                    url=item.get("url", ""),
                    source="arbeitnow",
                    posted_at=datetime.fromtimestamp(created, timezone.utc).replace(tzinfo=None),
                    description=_strip_html(item.get("description", "")) or None,
                ))
            if not data:
                break
        return results
