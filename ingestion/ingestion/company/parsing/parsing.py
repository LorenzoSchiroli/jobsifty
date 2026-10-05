import json
import logging
from groq import Groq

from ingestion.company.models import CompanyExtraction
from ingestion.company.parsing import rules
from ingestion.company.parsing.extractor import extract_jsonld, extract_with_llm
from ingestion.utils.text import clean_html, MAX_COMPANY_DESCRIPTION_CHARS

logger = logging.getLogger(__name__)

_ALL_FIELDS = [
    "country", "founded_year", "employee_count",
    "industry", "is_consulting", "is_startup", "review_score", "review_count",
]


def _missing(data: dict) -> list[str]:
    return [f for f in _ALL_FIELDS if data.get(f) is None]


def _build_sources(text_dict: dict) -> dict:
    sources: dict = {}

    pages = []
    for html in filter(None, [text_dict.get("website_html")] + (text_dict.get("extra_htmls") or [])):
        if text := clean_html(html, aggressive=False):
            pages.append(text)
    if pages:
        sources["company_pages"] = pages

    if html := text_dict.get("linkedin_html"):
        if text := clean_html(html, aggressive=False):
            sources["linkedin"] = text

    if snippets := text_dict.get("review_snippets"):
        sources["glassdoor_reviews"] = snippets

    if html := text_dict.get("financial_html"):
        if text := clean_html(html, aggressive=False):
            sources["financial_page"] = text

    if snippets := text_dict.get("financial_snippets"):
        sources["financial_snippets"] = snippets

    return sources


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
    return bool(_missing(merged)) or merged.get("topic") is None or merged.get("financial_health_score") is None


def parse(name: str, location: str, text_dict: dict, client: Groq | None = None) -> CompanyExtraction:
    """Deterministic sources first (JSON-LD → Wikidata → Glassdoor → heuristics); the LLM only fills what is left."""
    merged: dict = {}

    if website_html := text_dict.get("website_html"):
        _fill(merged, extract_jsonld(website_html))

    if url := text_dict.get("website_url"):
        merged["website"] = url
    if url := text_dict.get("linkedin_url"):
        merged["linkedin_url"] = url

    wikidata = text_dict.get("wikidata") or {}
    _fill(merged, {k: wikidata.get(k) for k in ("founded_year", "employee_count", "country", "industry")})
    _fill(merged, rules.parse_glassdoor(text_dict.get("review_snippets")))
    _apply_heuristics(merged, name, location, wikidata)

    sources = _build_sources(text_dict)

    if client is not None and sources and _wants_llm(merged):
        if extraction := extract_with_llm(name, location, client, sources):
            _fill(merged, extraction.model_dump())
            _apply_heuristics(merged, name, location, wikidata)

    if merged.get("topic") is None and wikidata.get("description"):
        merged["topic"] = wikidata["description"]

    if sources:
        merged["description"] = json.dumps(sources, ensure_ascii=False)[:MAX_COMPANY_DESCRIPTION_CHARS]
    elif merged.get("description") is None and wikidata.get("description"):
        merged["description"] = wikidata["description"]

    return CompanyExtraction.model_validate(merged)


def parse_financial(
    name: str, location: str, info: CompanyExtraction, text_dict: dict, client: Groq | None,
) -> CompanyExtraction:
    if client is None:
        return info
    targeted = extract_with_llm(name, location, client, _build_sources(text_dict))
    if targeted is None:
        return info
    data = info.model_dump()
    for field in ("financial_health_score", "financial_health_rationale"):
        if (val := getattr(targeted, field)) is not None:
            data[field] = val
    return CompanyExtraction.model_validate(data)
