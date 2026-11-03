"""One search variant: retrieval, rerank and a Postgres lookup, as candidates."""
import asyncio
import uuid

from pydantic import BaseModel
from sqlalchemy import select

from backend.models import Job
from backend.search.advanced.brief import Brief, BriefItem, hard_filters, satisfies
from backend.search.advanced.budget import CANDIDATE_WINDOW
from backend.search.advanced.observation import Candidate, canonical_country, job_country
from backend.search.filters import SearchFilters
from backend.search.models_lifecycle import encode_query
from backend.search.retrieval import hybrid_retrieve
from backend.search.reranker import rerank_scored


class VariantFilters(BaseModel):
    company_country: str | None = None
    location_type: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    languages_required: list[str] | None = None
    is_consulting: bool | None = None
    is_startup: bool | None = None
    employee_count: str | None = None
    min_review_score: float | None = None
    industry: str | None = None

    def to_search_filters(self, query: str, max_age_hours: int | None) -> SearchFilters:
        values = self.model_dump(exclude_none=True)
        if "company_country" in values:
            values["country"] = values.pop("company_country")
        return SearchFilters(semantic_query=query, max_age_hours=max_age_hours, **values)


class SearchVariant(BaseModel):
    query: str
    filters: VariantFilters = VariantFilters()
    max_age_hours: int | None = 72
    probe: str | None = None  # text of the stated item this search lifts


def _same_country(a: str | None, b: str | None) -> bool:
    return (canonical_country(a) or a or "").casefold() == (canonical_country(b) or b or "").casefold()


def matches_soft(c: Candidate, variant: SearchVariant) -> bool:
    """Whether the candidate equals every soft filter the variant set.

    employee_count, min_review_score and industry are not kept on candidates and are not checked.
    """
    f = variant.filters
    for name in ("location_type", "seniority", "employment_type", "is_consulting", "is_startup"):
        wanted = getattr(f, name)
        if wanted is not None and getattr(c, name) != wanted:
            return False
    if f.company_country is not None and not _same_country(c.company_country, f.company_country):
        return False
    if f.languages_required:
        wanted_langs = {l.casefold() for l in f.languages_required}
        if not wanted_langs & {l.casefold() for l in c.languages}:
            return False
    return True


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
            posted_at=src.get("posted_at") or "",  # may be empty; rendering treats it as unknown age
            text=text,
            search_index=search_index,
        )
        if item is not None and satisfies(item, c.fields()) is not True:
            c.breaks = [variant.probe]
        candidates.append(c)
    return candidates
