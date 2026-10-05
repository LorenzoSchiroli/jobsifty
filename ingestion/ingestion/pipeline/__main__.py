import argparse
import logging
import os
import sys
from groq import Groq
from tqdm import tqdm
from ingestion.offer.offer import enrich_all
from ingestion.company.company import enrich as enrich_company
from ingestion.client import post_job, post_company

logger = logging.getLogger(__name__)

_IDENTITY_FIELDS = {"id", "name", "created_at", "updated_at"}
# More failed job posts than this fails the run, so the CronJob shows Failed.
MAX_POST_ERROR_RATE = 0.2


def _enrichable(company: dict) -> dict:
    return {k: v for k, v in company.items() if k not in _IDENTITY_FIELDS}


def is_enrichment_needed(company: dict) -> bool:
    values = list(_enrichable(company).values())
    if not values:
        return False
    return sum(1 for v in values if v is None) >= len(values) / 2


def has_enriched_fields(profile: dict) -> bool:
    return any(v is not None for v in _enrichable(profile).values())


def run(query: str, hours: int) -> int:
    """Run the pipeline; returns the process exit code."""
    api_key = os.environ.get("GROQ_API_KEY")
    groq = Groq(api_key=api_key) if api_key else None
    if groq is None:
        logger.info("GROQ_API_KEY not set — company enrichment runs without the LLM")

    logger.info("[1/3] Scraping & parsing offers: %r, last %dh", query, hours)
    offers = enrich_all(query, hours)
    logger.info("Scraped %d offers", len(offers))

    logger.info("[2/3] Posting %d jobs...", len(offers))
    new_jobs = 0
    errors = 0
    company_locations: dict[str, str] = {}
    for offer in tqdm(offers, desc="Posting jobs", unit="job"):
        try:
            status, _ = post_job(offer)
            if status == 201:
                new_jobs += 1
        except Exception as e:
            logger.warning("Failed to post job %r: %s", offer.url, e)
            errors += 1
            continue
        if offer.company not in company_locations or (
            company_locations[offer.company] == "" and offer.location
        ):
            company_locations[offer.company] = offer.location or ""
    logger.info("Jobs: %d new, %d existing, %d errors", new_jobs, len(offers) - new_jobs - errors, errors)

    logger.info("[3/3] Upserting & enriching %d companies...", len(company_locations))
    enriched = 0
    enrich_failed = 0
    new_companies = 0
    for name, location in tqdm(company_locations.items(), desc="Companies", unit="company"):
        try:
            status, record = post_company({"name": name})
        except Exception as e:
            logger.warning("Failed to upsert company %r: %s", name, e)
            continue
        if status == 201:
            new_companies += 1
        if is_enrichment_needed(record):
            try:
                logger.info("Enriching company %r...", name)
                profile, _ = enrich_company(name, location, groq)
                data = profile.model_dump(mode="json")
                if has_enriched_fields(data):
                    post_company(data)
                    enriched += 1
                else:
                    enrich_failed += 1
            except Exception as e:
                logger.warning("Failed to enrich company %r: %s", name, e)
                enrich_failed += 1
    logger.info(
        "Companies: %d new, %d enriched, %d enrichment failed, out of %d unique",
        new_companies, enriched, enrich_failed, len(company_locations),
    )

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
