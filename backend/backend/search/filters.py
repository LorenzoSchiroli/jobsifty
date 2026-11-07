from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

from backend.search.countries import canonical_country, country_values

_BOOST = 2.0

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
        values = country_values(filters.country)
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


class ActiveFilters(BaseModel):
    """What a search filtered on, as short labels for the page: `required` every result meets,
    `preferred` ranks matching jobs higher without excluding others."""
    required: list[str] = []
    preferred: list[str] = []


def window_label(hours: int) -> str:
    """A posting window as the user reads it: "last 3 days"."""
    days = round(hours / 24)
    return f"last {hours} hours" if hours < 24 else f"last {days} day{'' if days == 1 else 's'}"


def _any(value: str | list[str]) -> str:
    return " or ".join(value) if isinstance(value, list) else value


def _usable(value) -> bool:
    """A value build_clauses filters on: an alternative "a|b" string is ignored there, so here too."""
    if isinstance(value, list):
        return bool(value)
    return value is not None and (not isinstance(value, str) or "|" not in value)


def describe(filters: SearchFilters) -> ActiveFilters:
    """The labels of what `filters` applies: the field filters are required when strict, otherwise
    preferred (build_clauses); the posting window is always required (age_cutoff)."""
    labels = []
    for field, label in [
        ("seniority", lambda v: _any(v)),
        ("location_type", lambda v: _any(v)),
        ("employment_type", lambda v: _any(v)),
        ("languages_required", lambda v: ", ".join(lang.capitalize() for lang in v) + " required"),
        ("country", lambda v: f"company in {canonical_country(v) or v}"),
        ("is_startup", lambda v: "startup" if v else "not a startup"),
        ("is_consulting", lambda v: "consulting" if v else "no consulting"),
        ("min_review_score", lambda v: f"reviews ≥ {v:g}"),
        ("min_financial_health_score", lambda v: f"financial health ≥ {v}"),
    ]:
        value = getattr(filters, field)
        if _usable(value):
            labels.append(label(value))
    window = [f"posted in the {window_label(filters.max_age_hours)}"] if filters.max_age_hours else []
    if filters.strict:
        return ActiveFilters(required=[*labels, *window])
    return ActiveFilters(required=window, preferred=labels)


def age_cutoff(filters: SearchFilters) -> str | None:
    """The oldest posted_at the search returns, or None for no limit."""
    if filters.max_age_hours is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(hours=filters.max_age_hours)).isoformat()
