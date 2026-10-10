import json
import logging
import os
import re
import time
from bs4 import BeautifulSoup
from groq import APIStatusError, Groq

from ingestion.company.models import CompanyExtraction

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("GROQ_MODEL_BASE", "")

_ORG_TYPES = ("Organization", "LocalBusiness", "Corporation")

_LLM_PROMPT = (
    "Below are web search results about an employer that posted a job. "
    "Return ONLY a JSON object with these fields, using null for anything the results do not state:\n"
    '{{"topic": str, "country": str, "founded_year": int, "employee_count": str, "industry": str, '
    '"is_consulting": bool, "is_startup": bool, "financial_health_score": int, "financial_health_rationale": str}}\n\n'
    "- topic: one specific sentence on what the company does and sells, in English "
    "(e.g. 'Barclays is a British universal bank providing retail and investment banking services').\n"
    "- country: country of the headquarters, in English.\n"
    "- employee_count: the number of employees as a number or range like '51-200'; "
    "never users, customers or freelancers.\n"
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
# Groq's free tier allows 8,000 tokens per minute; a call is ~1.5k tokens.
_MIN_SECONDS_BETWEEN_CALLS = 12
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


# Circuit breaker: once Groq says the key is unusable (401/403) or the daily quota is gone,
# every further call this run would fail too — stop calling instead of burning time.
_RATE_LIMIT_TRIP_AFTER = 3
_breaker_open = False
_consecutive_rate_limits = 0
_last_call = 0.0


def breaker_open() -> bool:
    return _breaker_open


def reset_breaker() -> None:
    global _breaker_open, _consecutive_rate_limits, _last_call
    _breaker_open = False
    _consecutive_rate_limits = 0
    _last_call = 0.0


def _trip(reason: str) -> None:
    global _breaker_open
    if not _breaker_open:
        _breaker_open = True
        logger.warning("LLM circuit breaker tripped (%s); skipping LLM calls for the rest of this run", reason)


def _record_failure(e: Exception) -> None:
    global _consecutive_rate_limits
    status = e.status_code if isinstance(e, APIStatusError) else None
    if status in (401, 403):
        _trip(f"HTTP {status}")
    elif status == 429:
        _consecutive_rate_limits += 1
        message = str(e).lower()
        if "per day" in message or "(tpd)" in message or "(rpd)" in message:
            _trip("daily quota exhausted")
        elif _consecutive_rate_limits >= _RATE_LIMIT_TRIP_AFTER:
            _trip(f"{_consecutive_rate_limits} consecutive rate limits")


def _wait_for_slot() -> None:
    global _last_call
    if (wait := _last_call + _MIN_SECONDS_BETWEEN_CALLS - time.monotonic()) > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


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


def extract_with_llm(name: str, location: str, client: Groq | None, sources: str) -> CompanyExtraction | None:
    """LLM extraction; None when there is no client or input, the breaker is open, or the call fails."""
    global _consecutive_rate_limits
    if client is None or _breaker_open or not sources:
        return None

    prompt = _LLM_PROMPT.format(name=name, location=location or "unknown", sources=sources)
    _wait_for_slot()
    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning("LLM extraction failed: %s", e)
        _record_failure(e)
        return None
    _consecutive_rate_limits = 0

    try:
        data = json.loads(_strip_markdown_json(response.choices[0].message.content))
        data["financial_health_score"] = _valid_score(data.get("financial_health_score"))
        if data["financial_health_score"] is None:
            data["financial_health_rationale"] = None
        return CompanyExtraction.model_validate(data)
    except Exception as e:
        logger.warning("LLM returned unusable output: %s", e)
        return None
