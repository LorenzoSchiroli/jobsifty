import argparse
import logging
import os
import sys
import time
from tqdm import tqdm
from ingestion.offer.offer import enrich_all
from ingestion.company.company import enrich as enrich_company
from ingestion.client import get_pending_companies, post_company, post_job
from ingestion.company.scraping.searcher import pick_website, pool as search_pool
from ingestion.llm import LLMPool

logger = logging.getLogger(__name__)

# Minutes a run spends enriching companies (~20 s each), after the jobs are posted. Quotas
# are the provider pools' business; this keeps the run inside the CronJob's 30 minutes.
DEFAULT_ENRICH_MINUTES = 15
# Pending companies fetched per run: more than fit in the time
_PENDING_FETCH = 200
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


def _enrich_pending(llm: LLMPool | None, board_facts: dict[str, dict], minutes: float) -> None:
    """Search and enrich the companies the backend says are most due. Best effort: a failure
    here is logged and never fails the run; whatever is not done waits for the next run.

    The backend lists the employers of the newest jobs first, so this run's companies come
    before the backlog, which fills the remaining time.
    """
    if minutes <= 0:
        logger.info("Company enrichment disabled (0 minutes)")
        return
    deadline = time.monotonic() + minutes * 60
    if configured := search_pool().configured():
        logger.info("Search providers: %s", ", ".join(configured))
    else:
        logger.warning("No search provider key set: companies are enriched without web search")
    try:
        pending = get_pending_companies(_PENDING_FETCH)
    except Exception as e:
        logger.warning("Could not fetch companies to enrich: %s", e)
        return

    filled = empty = failed = skipped = 0
    for i, company in enumerate(pending):
        if time.monotonic() >= deadline:
            skipped = len(pending) - i
            logger.info("Enrichment time is up; %d companies left for later", skipped)
            break
        # An attempt without search or LLM finds little and still counts towards the
        # retry limit, so the rest wait for a run where both are back.
        if search_pool().down() or (llm is not None and llm.down()):
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
            profile, _ = enrich_company(company["name"], company.get("location") or "", llm, known)
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
        "Company enrichment: %d pending, %d filled, %d nothing found, %d failed, %d skipped; searches: %s; LLM: %s",
        len(pending), filled, empty, failed, skipped, search_pool().stats(), llm.stats() if llm else "none",
    )


def run(query: str, hours: int) -> int:
    """Run the pipeline; returns the process exit code."""
    llm = LLMPool.from_env()
    if llm.configured():
        logger.info("LLM providers: %s", ", ".join(llm.configured()))
    else:
        logger.info("No LLM provider key set — company enrichment runs without the LLM")
        llm = None
    minutes = float(os.environ.get("COMPANY_ENRICH_MINUTES", DEFAULT_ENRICH_MINUTES))

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

    logger.info("[4/4] Enriching pending companies for up to %g minutes...", minutes)
    board_facts = {_key(name): {f: e[f] for f in _BOARD_FIELDS if e.get(f)} for name, e in companies.items()}
    _enrich_pending(llm, board_facts, minutes)

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
