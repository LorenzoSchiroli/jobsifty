"""One search variant: retrieval, rerank and a Postgres lookup, as candidates."""
import asyncio
import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select

from backend.live import job_live_days
from backend.models import Job
from backend.search.advanced.brief import Brief, BriefItem, hard_filters, satisfies
from backend.search.advanced.budget import CANDIDATE_WINDOW
from backend.search.advanced.observation import Candidate, job_country
from backend.search.advanced.vocabulary import (
    CHOICE_FIELDS,
    EmploymentType,
    Language,
    LocationType,
    Seniority,
    normalise_choice,
    normalise_country,
    normalise_languages,
)
from backend.search.countries import same_country
from backend.search.filters import SearchFilters
from backend.search.models_lifecycle import encode_query
from backend.search.retrieval import hybrid_retrieve
from backend.search.reranker import rerank_scored


class VariantFilters(BaseModel):
    """Soft filters of one variant: they rank matches higher but exclude nothing. Values as in the brief."""

    company_country: str | None = None
    location_type: LocationType | list[LocationType] | None = None
    seniority: Seniority | list[Seniority] | None = None
    employment_type: EmploymentType | list[EmploymentType] | None = None
    languages_required: list[Language] | None = None
    is_consulting: bool | None = None
    is_startup: bool | None = None
    min_review_score: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name in CHOICE_FIELDS:
            if data.get(name) is not None:
                data[name] = normalise_choice(name, data[name])
        if data.get("languages_required") is not None:
            data["languages_required"] = normalise_languages(data["languages_required"])
        if data.get("company_country") is not None:
            data["company_country"] = normalise_country(data["company_country"])
        return data

    def to_search_filters(self, query: str, max_age_hours: int | None) -> SearchFilters:
        values = self.model_dump(exclude_none=True)
        if "company_country" in values:
            values["country"] = values.pop("company_country")
        return SearchFilters(semantic_query=query, max_age_hours=max_age_hours, **values)


class SearchVariant(BaseModel):
    query: str
    filters: VariantFilters = VariantFilters()
    max_age_hours: int | None = None  # None: the run's window (search_jobs sets the window every search runs on)
    probe: str | None = None  # text of the stated item this search lifts

    @field_validator("max_age_hours", mode="after")
    @classmethod
    def _cap_window(cls, hours: int | None) -> int | None:
        """The index holds only live jobs, so a wider window means the live window."""
        return None if hours is None else min(hours, job_live_days() * 24)


def _any_of(actual: str | None, wanted: str | list[str]) -> bool:
    options = [wanted] if isinstance(wanted, str) else wanted
    return actual is not None and actual.casefold() in {o.casefold() for o in options}


def has_soft_filters(variant: SearchVariant) -> bool:
    return bool(variant.filters.model_dump(exclude_none=True))


def matches_soft(c: Candidate, variant: SearchVariant) -> bool:
    """Whether the candidate meets every soft filter the variant set (a list: any of its values)."""
    f = variant.filters
    for name in CHOICE_FIELDS:
        wanted = getattr(f, name)
        if wanted is not None and not _any_of(getattr(c, name), wanted):
            return False
    for name in ("is_consulting", "is_startup"):
        wanted = getattr(f, name)
        if wanted is not None and getattr(c, name) != wanted:
            return False
    if f.company_country is not None and not same_country(c.company_country, f.company_country):
        return False
    if f.min_review_score is not None and (c.review_score is None or c.review_score < f.min_review_score):
        return False
    if f.languages_required:
        wanted_langs = {l.casefold() for l in f.languages_required}
        if not wanted_langs & {l.casefold() for l in c.languages}:
            return False
    return True


def soft_matches(variant: SearchVariant, candidates: list[Candidate]) -> int | None:
    """How many candidates meet every soft filter; None when the variant set none."""
    if not has_soft_filters(variant):
        return None
    return sum(matches_soft(c, variant) for c in candidates)


def _probed_item(brief: Brief, probe: str) -> BriefItem | None:
    return next((i for i in brief.locked() if i.field is not None and i.text.casefold() == probe.casefold()), None)


async def run_variant(
    *,
    variant: SearchVariant,
    brief: Brief,
    search_index: int,
    biencoder,
    reranker,
    os_client,
    session_factory,
    now: datetime,
) -> list[Candidate]:
    item = _probed_item(brief, variant.probe) if variant.probe else None
    if variant.probe and item is None:
        raise ValueError(f"probe {variant.probe!r} matches no locked requirement of the brief")
    hard = hard_filters(brief, lifted=variant.probe)
    filters = variant.filters.to_search_filters(variant.query, variant.max_age_hours)
    embedding = await asyncio.to_thread(encode_query, biencoder, variant.query)
    hits = await hybrid_retrieve(os_client, embedding, filters, size=CANDIDATE_WINDOW, hard=hard)
    ranked = await asyncio.to_thread(
        rerank_scored, reranker, hits, variant.query, embedding, CANDIDATE_WINDOW
    )
    ids = [uuid.UUID(hit["_source"]["job_id"]) for hit, _ in ranked]
    async with session_factory() as session:
        rows = await session.execute(
            select(Job.id, Job.location, Job.office, Job.salary_range).where(Job.id.in_(ids))
        )
        extra = {str(r.id): r for r in rows}

    candidates = []
    for hit, score in ranked:
        src = hit["_source"]
        row = extra.get(src["job_id"])
        chunks = src.get("chunks") or []
        text = next((c["text"] for c in chunks if c.get("summary") and c.get("text")), None) or next(
            (c["text"] for c in chunks if c.get("text")), ""
        )
        c = Candidate(
            job_id=src["job_id"],
            score=score,
            title=src.get("title") or "",
            company=src.get("company_name"),
            location=row.location if row else None,
            job_country=job_country(row.location, row.office) if row else None,
            company_country=src.get("country"),
            location_type=src.get("location_type"),
            seniority=src.get("seniority"),
            employment_type=src.get("employment_type"),
            languages=list(src.get("languages_required") or []),
            salary=row.salary_range if row else None,
            is_startup=src.get("is_startup"),
            is_consulting=src.get("is_consulting"),
            review_score=src.get("review_score"),
            posted_at=src.get("posted_at") or "",  # may be empty; rendering treats it as unknown age
            text=text,
            search_index=search_index,
        )
        if item is not None and satisfies(item, c.fields(now)) is not True:
            c.breaks = [item.text]  # the brief's own text, not the probe as the agent typed it
        candidates.append(c)
    return candidates
