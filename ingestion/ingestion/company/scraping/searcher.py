"""Company web search through Tavily: two searches per company.

1. "<name> <city> <country> company" with Tavily's short answer: the official
   website, the LinkedIn page and a few facts (founded, HQ, size) about the
   employer. The job's city disambiguates namesakes.
2. "<name> glassdoor reviews", restricted to Glassdoor: rating and review count.

Every call costs one credit of a small monthly allowance, so the caller decides
how many companies to search per run; a quota or key error stops searching for
the rest of the run.
"""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_GLASSDOOR_DOMAINS = ["glassdoor.com", "glassdoor.co.uk", "glassdoor.de", "glassdoor.fr", "glassdoor.it", "glassdoor.es", "glassdoor.nl"]
# 401 bad key, 432 plan limit, 433 pay-as-you-go limit: no later call this run can succeed.
_FATAL_STATUSES = {401, 403, 432, 433}

# Never an employer's own website: social, job boards, reviews, directories and data aggregators.
_BLOCKED_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "xing.com",
    "glassdoor.", "indeed.", "kununu.com", "stepstone.", "welcometothejungle.com", "wellfound.com", "builtin.com",
    "bloomberg.com", "wikipedia.org", "wikidata.org", "yahoo.com", "reuters.com",
    "crunchbase.com", "pitchbook.com", "zoominfo.com", "dnb.com", "dun.com", "kompass.com", "northdata.",
    "opencorporates.com", "companieshouse.gov.uk", "find-and-update.company-information.service.gov.uk",
    "handelsregister.de", "yellowpages.com", "yelp.com", "manta.com", "hoovers.com",
    "leadiq.com", "rocketreach.co", "tracxn.com", "globaldata.com", "craft.co", "owler.com", "cbinsights.com",
    "signalhire.com", "apollo.io", "theorg.com", "contactout.com", "lusha.com", "datanyze.com", "websummit.com",
}
_LEGAL_SUFFIXES = re.compile(
    r"\(.*?\)|,|\b(?:inc|ltd|llc|plc|gmbh|ag|sa|sas|sarl|srl|spa|bv|nv|oy|ab|as|aps|kg|co|corp|limited|group)\b\.?",
    re.IGNORECASE,
)
_NOT_A_PLACE = re.compile(r"remote|home|thuis|télétravail|anywhere", re.IGNORECASE)

_breaker_open = False
_searches = 0


@dataclass
class CompanySearch:
    website: str | None = None
    linkedin_url: str | None = None
    answer: str | None = None
    snippets: list[str] = field(default_factory=list)
    review_snippets: list[str] = field(default_factory=list)


def breaker_open() -> bool:
    return _breaker_open


def searches_used() -> int:
    return _searches


def reset() -> None:
    global _breaker_open, _searches
    _breaker_open = False
    _searches = 0


def _tavily(query: str, **options) -> dict | None:
    """One Tavily search; None on any failure (logged with the response body)."""
    global _breaker_open, _searches
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or _breaker_open:
        return None
    _searches += 1
    try:
        resp = requests.post(
            _TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "search_depth": "basic", **options},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.warning("Tavily search failed for %r: %s", query, e)
        return None
    if resp.status_code != 200:
        logger.warning("Tavily search failed for %r: %s %s", query, resp.status_code, resp.text[:200])
        if resp.status_code in _FATAL_STATUSES:
            _breaker_open = True
            logger.warning("Tavily unavailable (HTTP %s); no more searches this run", resp.status_code)
        return None
    return resp.json()


def location_hint(location: str | None) -> str:
    """'Paris, Île-de-France, France' → 'Paris France'; remote/home markers dropped."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip() and not _NOT_A_PLACE.search(p)]
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1]}"


def _name_keys(name: str) -> tuple[str, str | None]:
    """The name squashed for domain matching, plus its first word when distinctive enough."""
    words = [w for w in re.findall(r"[a-z0-9]+", _LEGAL_SUFFIXES.sub(" ", name.lower())) if len(w) > 1]
    first = words[0] if words and len(words[0]) >= 5 else None
    return "".join(words), first


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_blocked(url: str) -> bool:
    domain = _domain(url)
    return any(blocked in domain for blocked in _BLOCKED_DOMAINS)


def pick_website(name: str, urls: list[str]) -> str | None:
    """The first result whose domain contains the company name, as the site root.

    Search results for small employers are mostly directories and aggregators;
    leaving the website empty beats storing one of those.
    """
    full, first = _name_keys(name)
    if not full:
        return None
    candidates = [u for u in urls if u.startswith("http") and not _is_blocked(u)]
    # the whole name first: "siemens healthineers" must prefer siemens-healthineers.com over siemens.com
    for key in [full] + ([first] if first else []):
        for url in candidates:
            if key in re.sub(r"[^a-z0-9]", "", _domain(url)):
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}"
    return None


def normalize_linkedin_url(url: str) -> str | None:
    if m := re.search(r"linkedin\.com/company/([^/?#]+)", url or ""):
        return f"https://www.linkedin.com/company/{m.group(1)}"
    return None


def search_company(name: str, location: str | None) -> CompanySearch:
    """Both searches in parallel; whatever fails is simply missing from the result."""
    company_query = " ".join(p for p in (name, location_hint(location), "company") if p)
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_company = ex.submit(_tavily, company_query, max_results=5, include_answer="basic")
        fut_reviews = ex.submit(
            _tavily, f"{name} glassdoor reviews", max_results=3, include_domains=_GLASSDOOR_DOMAINS,
        )
        company, reviews = fut_company.result(), fut_reviews.result()

    result = CompanySearch()
    hits = (company or {}).get("results", [])
    result.answer = (company or {}).get("answer") or None
    result.snippets = [h["content"] for h in hits if h.get("content")]
    result.website = pick_website(name, [h.get("url", "") for h in hits])
    result.linkedin_url = next(filter(None, (normalize_linkedin_url(h.get("url", "")) for h in hits)), None)
    result.review_snippets = [
        h["content"] for h in (reviews or {}).get("results", []) if "glassdoor." in h.get("url", "") and h.get("content")
    ]
    logger.debug("Tavily %r: website=%s linkedin=%s answer=%s glassdoor=%d",
                 name, result.website, result.linkedin_url, bool(result.answer), len(result.review_snippets))
    return result
