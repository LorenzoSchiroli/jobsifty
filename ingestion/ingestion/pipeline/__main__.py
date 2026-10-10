import argparse
import logging
import os
import sys
from groq import Groq
from tqdm import tqdm
from ingestion.offer.offer import enrich_all
from ingestion.company.company import enrich as enrich_company
from ingestion.client import get_pending_companies, post_company, post_job
from ingestion.company.parsing.extractor import breaker_open as llm_down
from ingestion.company.scraping.searcher import pick_website, pool as search_pool

logger = logging.getLogger(__name__)

# Companies enriched per run: 2 web searches + one Groq call (~2k tokens) each. Search
# runs on a pool of providers, so the limit is Groq's free 200k tokens/day: at the
# 2-hourly schedule, 6 per run is ~72 companies and ~150k tokens a day.
DEFAULT_COMPANIES_PER_RUN = 6
# More failed job posts than this fails the run, so the CronJob shows Failed.
MAX_POST_ERROR_RATE = 0.2

_BOARD_FIELDS = ("website", "linkedin_url", "industry", "description")


def _key(name: str) -> str:
    return name.lower().strip()


def new_fields(profile: dict, known: dict) -> list[str]:
    """Fields the enrichment found beyond what was already known."""
    return [k for k, v in profile.items() if k != "name" and v is not None and v != known.get(k)]


def _collect_companies(offers) -> dict[str, dict]:
    """One entry per company: the first non-empty location and job-board facts across its offers.

    A board's website goes through the same check as a searched one: boards also link
    careers pages (reduced to the homepage) and hiring platforms (dropped).
    """
    companies: dict[str, dict] = {}
    for offer in offers:
        entry = companies.setdefault(offer.company, {"location": ""})
        if not entry["location"] and offer.location:
            entry["location"] = offer.location
        info = dict(offer.company_info)
        if info.get("website"):
            info["website"] = pick_website(offer.company, [info["website"]])
        for field in _BOARD_FIELDS:
            if not entry.get(field) and info.get(field):
                entry[field] = info[field]
    return companies


def _post_jobs(offers) -> tuple[list, int, int]:
    posted, new_jobs, errors = [], 0, 0
    for offer in tqdm(offers, desc="Posting jobs", unit="job"):
        try:
            status, _ = post_job(offer)
        except Exception as e:
            logger.warning("Failed to post job %r: %s", offer.url, e)
            errors += 1
            continue
        posted.append(offer)
        new_jobs += status == 201
    return posted, new_jobs, errors


def _upsert_companies(companies: dict[str, dict]) -> int:
    """Store each company with what the job boards said about it: free, no search."""
    new = 0
    for name, entry in tqdm(companies.items(), desc="Companies", unit="company"):
        try:
            status, _ = post_company({"name": name, **{f: entry[f] for f in _BOARD_FIELDS if entry.get(f)}})
            new += status == 201
        except Exception as e:
            logger.warning("Failed to upsert company %r: %s", name, e)
    return new


def _enrich_pending(groq: Groq | None, board_facts: dict[str, dict], budget: int) -> None:
    """Search and enrich the companies the backend says are most due. Best effort: a failure
    here is logged and never fails the run; whatever is not done waits for the next run."""
    if budget <= 0:
        logger.info("Company enrichment disabled (budget 0)")
        return
    if configured := search_pool().configured():
        logger.info("Search providers: %s", ", ".join(configured))
    else:
        logger.warning("No search provider key set: companies are enriched without web search")
    try:
        pending = get_pending_companies(budget)
    except Exception as e:
        logger.warning("Could not fetch companies to enrich: %s", e)
        return

    filled = empty = failed = skipped = 0
    for i, company in enumerate(pending):
        # An attempt without search or LLM finds little and still counts towards the
        # retry limit, so the rest wait for a run where both are back.
        if search_pool().down() or (groq is not None and llm_down()):
            skipped = len(pending) - i
            logger.warning("Search or LLM unavailable for the rest of the run; %d companies left for later", skipped)
            break
        # A refresh (data over a year old) starts from this run's board facts only: the
        # stored values are what it is replacing.
        stored = {} if company.get("refresh") else {k: v for k, v in company.items() if v}
        # Older enrichments stored websites from unfiltered search results (registries, job
        # boards); such a site would be scraped as the employer's own, so it is not used.
        if stored.get("website") and not pick_website(company["name"], [stored["website"]]):
            del stored["website"]
        known = {**stored, **board_facts.get(company["name"], {})}
        try:
            logger.info("%s company %r (%d live jobs)...", "Refreshing" if company.get("refresh") else "Enriching",
                        company["name"], company["live_jobs"])
            profile, _ = enrich_company(company["name"], company.get("location") or "", groq, known)
            data = profile.model_dump(mode="json")
            post_company({**data, "enrichment_attempted": True})
        except Exception as e:
            logger.warning("Failed to enrich company %r: %s", company["name"], e)
            failed += 1
            continue
        found = new_fields(data, known)
        if found:
            filled += 1
            logger.info("  %r: %s", company["name"], ", ".join(found))
        else:
            empty += 1
    logger.info(
        "Company enrichment: %d pending, %d filled, %d nothing found, %d failed, %d skipped; searches: %s",
        len(pending), filled, empty, failed, skipped, search_pool().stats(),
    )


def run(query: str, hours: int) -> int:
    """Run the pipeline; returns the process exit code."""
    api_key = os.environ.get("GROQ_API_KEY")
    groq = Groq(api_key=api_key) if api_key else None
    if groq is None:
        logger.info("GROQ_API_KEY not set — company enrichment runs without the LLM")
    budget = int(os.environ.get("COMPANY_ENRICH_PER_RUN", DEFAULT_COMPANIES_PER_RUN))

    logger.info("[1/4] Scraping & parsing offers: %r, last %dh", query, hours)
    offers = enrich_all(query, hours)
    logger.info("Scraped %d offers", len(offers))

    logger.info("[2/4] Posting %d jobs...", len(offers))
    posted, new_jobs, errors = _post_jobs(offers)
    logger.info("Jobs: %d new, %d existing, %d errors", new_jobs, len(posted) - new_jobs, errors)

    companies = _collect_companies(posted)
    logger.info("[3/4] Upserting %d companies with job-board facts...", len(companies))
    new_companies = _upsert_companies(companies)
    logger.info("Companies: %d new, out of %d unique", new_companies, len(companies))

    logger.info("[4/4] Enriching up to %d pending companies...", budget)
    board_facts = {_key(name): {f: e[f] for f in _BOARD_FIELDS if e.get(f)} for name, e in companies.items()}
    _enrich_pending(groq, board_facts, budget)

    if offers and errors / len(offers) > MAX_POST_ERROR_RATE:
        logger.error("Job post error rate %.0f%% exceeds %.0f%%", 100 * errors / len(offers), 100 * MAX_POST_ERROR_RATE)
        return 1
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="Ingest offers and companies into backend")
    parser.add_argument("query", help="Job search query")
    parser.add_argument("--hours", type=int, default=72, help="How many hours back to search")
    args = parser.parse_args()
    sys.exit(run(args.query, args.hours))


if __name__ == "__main__":
    main()
