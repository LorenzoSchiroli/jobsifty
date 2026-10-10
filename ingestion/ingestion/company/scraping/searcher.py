"""Company web search: two searches per company through the provider pool.

1. "<name> <city> <country> company": the official website, the LinkedIn page and
   snippets about the employer (plus a short answer when Tavily serves it). The
   job's city disambiguates namesakes.
2. "<name> glassdoor reviews", restricted to Glassdoor: rating and review count.

The pool (`providers.py`) tries TinyFish, Exa and Tavily in order; a provider out of
credits or with a bad key is skipped for the rest of the run, a rate-limited one is
paused.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ingestion.company.scraping.providers import SearchPool

logger = logging.getLogger(__name__)

_GLASSDOOR_DOMAINS = ["glassdoor.com", "glassdoor.co.uk", "glassdoor.de", "glassdoor.fr", "glassdoor.it", "glassdoor.es", "glassdoor.nl"]
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

_pool: SearchPool | None = None


@dataclass
class CompanySearch:
    website: str | None = None
    linkedin_url: str | None = None
    answer: str | None = None
    snippets: list[str] = field(default_factory=list)
    review_snippets: list[str] = field(default_factory=list)


def pool() -> SearchPool:
    """The run's provider pool, built from the environment on first use."""
    global _pool
    if _pool is None:
        _pool = SearchPool.from_env()
    return _pool


def breaker_open() -> bool:
    """True when every configured provider is out for the rest of the run."""
    return pool().down()


def searches_used() -> int:
    return pool().calls()


def reset() -> None:
    """Forget the pool: the next search rebuilds it from the environment."""
    global _pool
    _pool = None


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
    search = pool().search
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_company = ex.submit(search, company_query, 5, None, True)
        fut_reviews = ex.submit(search, f"{name} glassdoor reviews", 3, _GLASSDOOR_DOMAINS)
        company, reviews = fut_company.result(), fut_reviews.result()

    result = CompanySearch()
    hits = company.hits if company else []
    result.answer = company.answer if company else None
    result.snippets = [h.text for h in hits if h.text]
    result.website = pick_website(name, [h.url for h in hits])
    result.linkedin_url = next(filter(None, (normalize_linkedin_url(h.url) for h in hits)), None)
    result.review_snippets = [h.text for h in (reviews.hits if reviews else []) if "glassdoor." in h.url and h.text]
    logger.debug("Search %r via %s/%s: website=%s linkedin=%s answer=%s glassdoor=%d",
                 name, company and company.provider, reviews and reviews.provider,
                 result.website, result.linkedin_url, bool(result.answer), len(result.review_snippets))
    return result
