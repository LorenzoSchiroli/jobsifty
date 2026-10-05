import logging
import time
from groq import Groq

from ingestion.company.models import CompanyProfile
from ingestion.company.scraping.scraping import scrape, scrape_financial
from ingestion.company.parsing.extractor import breaker_open
from ingestion.company.parsing.parsing import parse, parse_financial

logger = logging.getLogger(__name__)


def enrich(name: str, location: str, client: Groq | None) -> tuple[CompanyProfile, list[tuple[str, float]]]:
    timings: list[tuple[str, float]] = []

    def tick(label: str, t0: float) -> float:
        timings.append((label, time.perf_counter() - t0))
        return time.perf_counter()

    text_dict, scrape_timings = scrape(name, location)
    timings.extend(scrape_timings)

    t = time.perf_counter()
    info = parse(name, location, text_dict, client)
    tick("parse", t)

    llm_available = client is not None and not breaker_open()
    if llm_available and info.registration_numbers and info.financial_health_score in (None, 3):
        financial_text, financial_scrape_timings = scrape_financial(name, location, info.registration_numbers)
        timings.extend(financial_scrape_timings)
        t = time.perf_counter()
        info = parse_financial(name, location, info, financial_text, client)
        tick("parse financial", t)

    return CompanyProfile.model_validate({"name": name, **info.model_dump()}), timings
