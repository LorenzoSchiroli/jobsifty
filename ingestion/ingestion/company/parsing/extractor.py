import json
import logging
import re
from bs4 import BeautifulSoup

from ingestion.company.models import CompanyExtraction
from ingestion.llm import LLMPool

logger = logging.getLogger(__name__)

_ORG_TYPES = ("Organization", "LocalBusiness", "Corporation")

_LLM_PROMPT = (
    "Below are web search results about an employer that posted a job. "
    "Return ONLY a JSON object with these fields, using null for anything the results do not state:\n"
    '{{"topic": str, "country": str, "founded_year": int, "employee_count": str, "industry": str, '
    '"is_consulting": bool, "is_startup": bool, "financial_health_score": int, "financial_health_rationale": str}}\n\n'
    "- topic: one specific sentence on what the company does and sells, in English "
    "(e.g. 'Barclays is a British universal bank providing retail and investment banking services').\n"
    "- country: country of the headquarters, in English; not where it was founded, "
    "and not where this job or a local office is.\n"
    "- employee_count: the number of employees of this exact employer as a number or range like '51-200'; "
    "for a local branch or subsidiary (e.g. 'Acme Germany') its own staff, never the parent group's total "
    "(null when only the group's figure is given); never users, customers or freelancers.\n"
    "- is_consulting: true when it mainly sells consulting, outsourcing, staffing or recruiting.\n"
    "- is_startup: true for a young, venture-funded, growing company; null when unclear.\n"
    "- financial_health_score: 1 (insolvency, severe losses) to 5 (very profitable market leader), "
    "only from evidence such as funding, revenue growth, profits, layoffs or losses; "
    "null when the results contain no such evidence. "
    "financial_health_rationale: one English sentence citing that evidence, or null.\n"
    "If the results describe a different organisation with the same name (check the job location), "
    "return null for every field.\n\n"
    "Employer: {name}\n"
    "Job location: {location}\n\n"
    "Search results:\n{sources}"
)
_SNIPPET_CHARS = 400
_MAX_SNIPPETS = 5
_PAGE_CHARS = 2000

def _strip_markdown_json(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\n?```\s*$", "", stripped).strip()


def extract_jsonld(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") in _ORG_TYPES), {})
            if data.get("@type") not in _ORG_TYPES:
                continue

            if url := data.get("url"):
                result["website"] = url
            if description := data.get("description"):
                result["description"] = description
            if rating := data.get("aggregateRating"):
                try:
                    result["review_score"] = float(rating["ratingValue"])
                    result["review_count"] = int(rating["reviewCount"])
                except (KeyError, ValueError):
                    pass
            if address := data.get("address"):
                if country := address.get("addressCountry"):
                    result["country"] = country
            if employees := data.get("numberOfEmployees"):
                val = employees.get("value") if isinstance(employees, dict) else employees
                if val is not None:
                    result["employee_count"] = str(val)
            if founded := data.get("foundingDate"):
                try:
                    result["founded_year"] = int(str(founded)[:4])
                except ValueError:
                    pass
        except (json.JSONDecodeError, AttributeError):
            continue

    return result


def format_sources(
    answer: str | None, snippets: list[str], board_description: str | None = None, page_text: str | None = None,
) -> str:
    """The search answer, the top snippets and the job board's blurb, kept to ~1.5k tokens.

    The company's own page is added only without a search answer (search down or out of
    credits), so the LLM still has something about the employer to read.
    """
    lines = []
    if answer:
        lines.append(f"Summary: {answer}")
    if board_description:
        lines.append(f"Job board description: {board_description[:_SNIPPET_CHARS]}")
    lines += [f"- {s[:_SNIPPET_CHARS]}" for s in snippets[:_MAX_SNIPPETS]]
    if page_text and not answer:
        lines.append(f"Company website: {page_text[:_PAGE_CHARS]}")
    return "\n".join(lines)


def _valid_score(value):
    return value if isinstance(value, int) and 1 <= value <= 5 else None


def _parse_extraction(content: str) -> CompanyExtraction:
    """The model's JSON as a CompanyExtraction; raises on anything unusable, so the pool
    asks the next provider."""
    data = json.loads(_strip_markdown_json(content))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    data["financial_health_score"] = _valid_score(data.get("financial_health_score"))
    if data["financial_health_score"] is None:
        data["financial_health_rationale"] = None
    return CompanyExtraction.model_validate(data)


def extract_with_llm(name: str, location: str, llm: LLMPool | None, sources: str) -> CompanyExtraction | None:
    """LLM extraction; None without a pool or input, or when no provider gives a usable answer."""
    if llm is None or not sources:
        return None
    prompt = _LLM_PROMPT.format(name=name, location=location or "unknown", sources=sources)
    return llm.complete(prompt, _parse_extraction, what=f"company {name!r}")
