import logging
from groq import Groq

from ingestion.company.models import CompanyExtraction
from ingestion.company.parsing import rules
from ingestion.company.parsing.extractor import extract_jsonld, extract_with_llm, format_sources
from ingestion.utils.text import clean_html

logger = logging.getLogger(__name__)

_ALL_FIELDS = [
    "country", "founded_year", "employee_count",
    "industry", "is_consulting", "is_startup", "review_score", "review_count",
]


def _missing(data: dict) -> list[str]:
    return [f for f in _ALL_FIELDS if data.get(f) is None]


def _fill(merged: dict, data: dict) -> None:
    for k, v in data.items():
        if v is not None and merged.get(k) is None:
            merged[k] = v


def _apply_heuristics(merged: dict, name: str, location: str, wikidata: dict) -> None:
    _fill(merged, {
        "is_consulting": rules.detect_consulting(
            name, merged.get("industry"), merged.get("topic"), wikidata.get("description"),
        ),
        "is_startup": rules.detect_startup(merged.get("founded_year"), merged.get("employee_count")),
        "country": rules.country_from_location(location),
    })


def _wants_llm(merged: dict) -> bool:
    return bool(_missing(merged)) or merged.get("topic") is None


def parse(name: str, location: str, text_dict: dict, client: Groq | None = None) -> CompanyExtraction:
    """Deterministic sources first (job board → JSON-LD → Wikidata → Glassdoor); one small LLM
    call on the search results fills what is left, or rules on the search answer without it."""
    merged: dict = {}
    known = text_dict.get("known") or {}
    wikidata = text_dict.get("wikidata") or {}
    answer = text_dict.get("search_answer")

    if url := text_dict.get("website_url"):
        merged["website"] = url
    if url := text_dict.get("linkedin_url"):
        merged["linkedin_url"] = url
    _fill(merged, {"industry": known.get("industry")})
    if website_html := text_dict.get("website_html"):
        _fill(merged, extract_jsonld(website_html))
    _fill(merged, {k: wikidata.get(k) for k in ("founded_year", "employee_count", "country", "industry")})
    _fill(merged, rules.parse_glassdoor(text_dict.get("review_snippets")))
    # no job location yet: where the job is says less about the headquarters than the LLM does
    _apply_heuristics(merged, name, "", wikidata)

    extraction = None
    if client is not None and _wants_llm(merged):
        page_text = clean_html(text_dict["website_html"], aggressive=False) if text_dict.get("website_html") else None
        sources = format_sources(answer, text_dict.get("search_snippets") or [], known.get("description"), page_text)
        extraction = extract_with_llm(name, location, client, sources)
    if extraction is not None:
        _fill(merged, extraction.model_dump())
    else:
        _fill(merged, rules.parse_answer(answer))
    _apply_heuristics(merged, name, location, wikidata)

    if merged.get("topic") is None and wikidata.get("description"):
        merged["topic"] = wikidata["description"]
    _fill(merged, {"description": answer or known.get("description") or wikidata.get("description")})

    return CompanyExtraction.model_validate(merged)
