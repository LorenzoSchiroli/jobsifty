import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ingestion.company.scraping.fetcher import fetch_html, find_relevant_links
from ingestion.company.scraping.searcher import search_company_urls, search_financial
from ingestion.company.scraping.wikidata import lookup as wikidata_lookup

logger = logging.getLogger(__name__)

Timings = list[tuple[str, float]]


def scrape(name: str, location: str) -> tuple[dict, Timings]:
    timings: Timings = []

    def tick(label: str, t0: float) -> float:
        timings.append((label, time.perf_counter() - t0))
        return time.perf_counter()

    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_wikidata = ex.submit(wikidata_lookup, name)
        urls, review_snippets, financial_snippets = search_company_urls(name, location)
        wikidata = fut_wikidata.result()
    t = tick("scrape / search + wikidata", t)

    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_website   = ex.submit(fetch_html, urls["website"])   if "website"   in urls else None
        fut_financial = ex.submit(fetch_html, urls["financial"]) if "financial" in urls else None
        fut_linkedin  = ex.submit(fetch_html, urls["linkedin"])  if "linkedin"  in urls else None
        website_html   = fut_website.result()   if fut_website   else None
        financial_html = fut_financial.result() if fut_financial else None
        linkedin_html  = fut_linkedin.result()  if fut_linkedin  else None
    t = tick("scrape / fetch website+linkedin+financial", t)

    extra_htmls: list[str] = []
    if website_html:
        extra_urls = find_relevant_links(website_html, urls.get("website", ""))
        t = tick("scrape / find links", t)
        if extra_urls:
            with ThreadPoolExecutor(max_workers=3) as ex:
                extra_htmls = [h for h in ex.map(fetch_html, extra_urls) if h]
            t = tick("scrape / fetch extra pages", t)

    return {
        "website_url": urls.get("website"),
        "website_html": website_html,
        "extra_htmls": extra_htmls,
        "linkedin_url": urls.get("linkedin"),
        "linkedin_html": linkedin_html,
        "financial_url": urls.get("financial"),
        "financial_html": financial_html,
        "review_snippets": review_snippets,
        "financial_snippets": financial_snippets,
        "wikidata": wikidata,
    }, timings


def scrape_financial(name: str, location: str, registration_numbers: dict) -> tuple[dict, Timings]:
    timings: Timings = []

    def tick(label: str, t0: float) -> float:
        timings.append((label, time.perf_counter() - t0))
        return time.perf_counter()

    t = time.perf_counter()
    url, snippets = search_financial(name, location, registration_numbers)
    html = fetch_html(url) if url else None
    tick("scrape financial / search+fetch", t)

    return {
        "financial_url": url,
        "financial_html": html,
        "financial_snippets": snippets,
    }, timings
