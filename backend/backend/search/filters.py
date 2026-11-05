from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

_BOOST = 2.0

# Indexed company.country is often an ISO code (DE) while query parsing emits
# canonical names (Germany). Match both forms.
_COUNTRY_NAME_TO_ISO = {
    "United Kingdom": "GB",
    "United States": "US",
    "Germany": "DE",
    "France": "FR",
    "Spain": "ES",
    "Italy": "IT",
    "Netherlands": "NL",
    "Poland": "PL",
    "Portugal": "PT",
    "Ireland": "IE",
    "Switzerland": "CH",
    "Austria": "AT",
    "Belgium": "BE",
    "Sweden": "SE",
    "Canada": "CA",
}


def _country_values(country: str) -> list[str]:
    if country in _COUNTRY_NAME_TO_ISO:
        return [country, _COUNTRY_NAME_TO_ISO[country]]
    upper = country.upper()
    for name, iso in _COUNTRY_NAME_TO_ISO.items():
        if upper == iso:
            return [name, iso]
    return [country]


class SearchFilters(BaseModel):
    is_consulting: bool | None = None
    is_startup: bool | None = None
    industry: str | None = None
    country: str | None = None
    employee_count: str | None = None
    min_review_score: float | None = None
    min_financial_health_score: int | None = None
    # A list means any of its values (only the advanced agent sets one)
    employment_type: str | list[str] | None = None
    location_type: str | list[str] | None = None
    seniority: str | list[str] | None = None
    languages_required: list[str] | None = None
    # Jobs posted within this many hours, a hard limit; the query can widen it ("last 2 weeks")
    max_age_hours: int | None = 72
    semantic_query: str
    strict: bool = False


def build_clauses(filters: SearchFilters) -> list[dict]:
    strict = filters.strict

    def term(field: str, value: object) -> dict:
        return {"term": {field: value if strict else {"value": value, "boost": _BOOST}}}

    def terms(field: str, values: list) -> dict:
        return {"terms": {field: values} if strict else {field: values, "boost": _BOOST}}

    def rng(field: str, gte: object) -> dict:
        return {"range": {field: {"gte": gte} if strict else {"gte": gte, "boost": _BOOST}}}

    clauses: list[dict] = []
    for field, value in {
        "is_consulting": filters.is_consulting,
        "is_startup": filters.is_startup,
        "employment_type": filters.employment_type,
        "location_type": filters.location_type,
        "seniority": filters.seniority,
    }.items():
        if isinstance(value, list):
            if value:
                clauses.append(terms(field, value))
        elif value is not None and (not isinstance(value, str) or "|" not in value):
            clauses.append(term(field, value))
    if filters.country is not None and "|" not in filters.country:
        values = _country_values(filters.country)
        if len(values) == 1:
            clauses.append(term("country", values[0]))
        else:
            clauses.append(
                {"terms": {"country": values}}
                if strict
                else {"terms": {"country": values, "boost": _BOOST}}
            )
    if filters.min_review_score is not None:
        clauses.append(rng("review_score", filters.min_review_score))
    if filters.min_financial_health_score is not None:
        clauses.append(rng("financial_health_score", filters.min_financial_health_score))
    if filters.languages_required:
        langs = [lang.lower() for lang in filters.languages_required]
        clauses.append(
            {"terms": {"languages_required": langs}}
            if strict
            else {"terms": {"languages_required": langs, "boost": _BOOST}}
        )
    return clauses


def age_cutoff(filters: SearchFilters) -> str | None:
    """The oldest posted_at the search returns, or None for no limit."""
    if filters.max_age_hours is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(hours=filters.max_age_hours)).isoformat()
