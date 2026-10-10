import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ingestion.company.scraping.fetcher import fetch_html, find_relevant_links
from ingestion.company.scraping.searcher import search_company
from ingestion.company.scraping.wikidata import lookup as wikidata_lookup
from ingestion.utils.urls import registered_domain

logger = logging.getLogger(__name__)

Timings = list[tuple[str, float]]


def scrape(name: str, location: str, known: dict | None = None) -> tuple[dict, Timings]:
    """Collect raw material about a company.

    `known` holds what is already stored or came with the job (website, linkedin_url,
    industry, description). The website comes from, in order: what is known, the
    search, Wikidata's official website (it finds huk.de for HUK-COBURG, where no name
    check could). A known LinkedIn page wins over the search.

    Wikidata matches by name only, so it can return a namesake ("Synthesia" is a Czech
    chemicals maker there). When its website disagrees with the known or searched one,
    none of its facts are used.
    """
    known = known or {}
    timings: Timings = []

    def tick(label: str, t0: float) -> float:
        timings.append((label, time.perf_counter() - t0))
        return time.perf_counter()

    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_wikidata = ex.submit(wikidata_lookup, name)
        found = search_company(name, location)
        wikidata = fut_wikidata.result()
    t = tick("scrape / search + wikidata", t)

    expected = known.get("website") or found.website
    if expected and wikidata.get("website") and registered_domain(expected) != registered_domain(wikidata["website"]):
        logger.info("Wikidata entry for %r is a namesake (%s, expected %s); ignored", name, wikidata["website"], expected)
        wikidata = {}
    website = expected or wikidata.get("website")
    linkedin_url = known.get("linkedin_url") or found.linkedin_url

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_website = ex.submit(fetch_html, website) if website else None
        fut_linkedin = ex.submit(fetch_html, linkedin_url) if linkedin_url else None
        website_html = fut_website.result() if fut_website else None
        linkedin_html = fut_linkedin.result() if fut_linkedin else None
    t = tick("scrape / fetch website+linkedin", t)

    extra_htmls: list[str] = []
    if website_html:
        extra_urls = find_relevant_links(website_html, website)
        t = tick("scrape / find links", t)
        if extra_urls:
            with ThreadPoolExecutor(max_workers=3) as ex:
                extra_htmls = [h for h in ex.map(fetch_html, extra_urls) if h]
            t = tick("scrape / fetch extra pages", t)

    return {
        "website_url": website,
        "website_html": website_html,
        "extra_htmls": extra_htmls,
        "linkedin_url": linkedin_url,
        "linkedin_html": linkedin_html,
        "search_answer": found.answer,
        "search_snippets": found.snippets,
        "review_snippets": found.review_snippets,
        "wikidata": wikidata,
        "known": known,
    }, timings
