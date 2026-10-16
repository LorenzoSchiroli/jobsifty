"""freehire.me: an open-source tech job aggregator with a public, keyless JSON API.

It collects postings from ~50 sources, mostly employers' own ATS boards (Greenhouse,
Ashby, Workday, Teamtailor...) plus EURES and the Bundesagentur, with the full
description included. Its `reality` field flags postings re-posted under a fresh
date ("stale") and ones that never close ("likely-evergreen"); those are skipped.

The window is on `created_at`, when freehire first indexed a posting, newest first:
a quarter of postings are indexed 5+ hours after their `posted_at`, so a window on
`posted_at` would miss them in every run.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from ingestion.offer.scraping.filters import _strip_html, is_english
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.sources.base import Source

logger = logging.getLogger(__name__)

_API_URL = "https://freehire.me/api/v1/agent/jobs/search"
_HEADERS = {"User-Agent": "jobsifty-ingestion"}
_PAGE_SIZE = 50
_MAX_PAGES = 10
_SKIPPED_REALITY = {"stale", "likely-evergreen"}
# matches the backend's 30-day retention
_MAX_AGE = timedelta(days=30)


def _clean_url(url: str) -> str:
    """The posting URL without freehire's utm tracking parameters."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _utc(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _to_offer(item: dict, now: datetime) -> JobOffer | None:
    title = (item.get("title") or "").strip()
    url = item.get("url") or ""
    posted = _utc(item.get("posted_at"))
    if not title or not url or not is_english(title):
        return None
    if posted is None or posted < now - _MAX_AGE or item.get("closed_at"):
        return None
    if (item.get("reality") or {}).get("class") in _SKIPPED_REALITY:
        return None
    return JobOffer(
        title=title,
        company=(item.get("company") or "").strip(),
        location=item.get("location") or "",
        url=_clean_url(url),
        source=f"freehire:{item.get('source') or 'unknown'}",
        posted_at=posted,
        description=_strip_html(item.get("description") or "") or None,
    )


class FreehireSource(Source):
    def fetch(self, query: str, hours: int) -> list[JobOffer]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(hours=hours)
        params = {
            "q": query,
            "regions": "eu",  # includes the UK
            "sort": "created_at",  # newest first
            "include_description": "true",
            "description_format": "html",
            "semantic_ratio": "0",  # keyword search
            "limit": _PAGE_SIZE,
        }
        results: list[JobOffer] = []
        for page in range(_MAX_PAGES):
            try:
                resp = requests.get(_API_URL, params={**params, "offset": page * _PAGE_SIZE},
                                    headers=_HEADERS, timeout=30)
                resp.raise_for_status()
                items = resp.json().get("data") or []
            except Exception as e:
                # keep what earlier pages returned
                logger.warning("freehire fetch failed (page %d): %s", page + 1, e)
                break
            reached_cutoff = False
            for item in items:
                try:
                    created = _utc(item.get("created_at"))
                    if created is not None and created < cutoff:
                        reached_cutoff = True
                        break
                    if offer := _to_offer(item, now):
                        results.append(offer)
                except Exception as e:
                    logger.info("freehire job skipped: %s", e)
            if reached_cutoff or len(items) < _PAGE_SIZE:
                break
            time.sleep(1)
        return results
