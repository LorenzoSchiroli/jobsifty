import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from ingestion.offer.scraping.filters import is_latin_script
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scopes import Scope
from ingestion.offer.scraping.stored import StoredJobs
from ingestion.offer.scraping.sources.base import Source

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
_COUNTRIES = ["gb", "de", "fr", "nl", "es", "it", "at", "be"]


class AdzunaSource(Source):
    """One request per country (the free tier allows 250 a day): the scope's terms as
    `what_or`, newest first. Adzuna filters by whole days only, so the hours are cut here
    on its exact `created` time."""

    def fetch(self, scope: Scope, hours: int, stored: StoredJobs = StoredJobs()) -> list[JobOffer]:
        app_id = os.getenv("ADZUNA_APP_ID")
        app_key = os.getenv("ADZUNA_APP_KEY")
        if not app_id or not app_key:
            logger.warning("Adzuna skipped: ADZUNA_APP_ID or ADZUNA_APP_KEY not set")
            return []

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        results = []
        for country in _COUNTRIES:
            try:
                resp = requests.get(
                    _BASE_URL.format(country=country),
                    params={
                        "app_id": app_id,
                        "app_key": app_key,
                        "results_per_page": 50,
                        "what_or": " ".join(scope.terms),
                        "sort_by": "date",
                        "max_days_old": max(1, -(-hours // 24)),
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                for item in resp.json().get("results", []):
                    title = item.get("title", "")
                    if not is_latin_script(title):
                        continue
                    posted_at = None
                    created = item.get("created", "")
                    try:
                        posted_at = (datetime.fromisoformat(created.replace("Z", "+00:00"))
                                     .astimezone(timezone.utc).replace(tzinfo=None))
                    except (ValueError, AttributeError):
                        pass
                    if posted_at and posted_at < cutoff:
                        continue
                    results.append(JobOffer(
                        title=title,
                        company=item.get("company", {}).get("display_name", ""),
                        location=item.get("location", {}).get("display_name", ""),
                        url=item.get("redirect_url", ""),
                        source="adzuna",
                        posted_at=posted_at,
                        description=None,
                    ))
            except Exception as e:
                # requests errors embed the full URL, query string (app_id, app_key) included
                logger.warning("Adzuna fetch failed for %s: %s", country,
                               str(e).replace(app_key, "***").replace(app_id, "***"))

        return results
