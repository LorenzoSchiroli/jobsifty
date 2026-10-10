import logging
import time

from ingestion.company.models import CompanyProfile
from ingestion.company.scraping.scraping import scrape
from ingestion.company.parsing.parsing import parse
from ingestion.llm import LLMPool

logger = logging.getLogger(__name__)


def enrich(
    name: str, location: str, llm: LLMPool | None, known: dict | None = None,
) -> tuple[CompanyProfile, list[tuple[str, float]]]:
    text_dict, timings = scrape(name, location, known)

    t = time.perf_counter()
    info = parse(name, location, text_dict, llm)
    timings.append(("parse", time.perf_counter() - t))

    return CompanyProfile.model_validate({"name": name, **info.model_dump()}), timings
