import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Company, Job, Outbox
from backend.outbox.worker import RECONCILE_WINDOW_DAYS
from backend.schemas import CompanyRequest, CompanyResponse, PendingCompany

router = APIRouter(prefix="/companies", tags=["companies"])

# An attempt that found nothing is retried after this pause, at most this many times.
ENRICH_RETRY_DAYS = 7
ENRICH_MAX_ATTEMPTS = 3


def _normalize(name: str) -> str:
    return name.lower().strip()


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, list) and not value:
        return True
    return False


@router.post("/", response_model=CompanyResponse, status_code=201)
async def upsert_company(body: CompanyRequest, response: Response, session: AsyncSession = Depends(get_session)):
    normalized = _normalize(body.name)
    result = await session.execute(select(Company).where(Company.name == normalized))
    company = result.scalar_one_or_none()

    if company is None:
        data = body.model_dump(exclude={"enrichment_attempted"})
        data["name"] = normalized
        company = Company(**data)
        session.add(company)
        response.status_code = 201
    else:
        data = body.model_dump(exclude={"name", "enrichment_attempted"})
        for field, value in data.items():
            if not _is_empty(value) and _is_empty(getattr(company, field)):
                setattr(company, field, value)
        response.status_code = 200

    if body.enrichment_attempted:
        company.enriched_at = datetime.now(timezone.utc)
        company.enrich_attempts = (company.enrich_attempts or 0) + 1

    await session.flush()

    session.add(Outbox(
        event_type="company_upserted",
        entity_id=company.id,
    ))

    await session.commit()
    await session.refresh(company)
    return company


@router.get("/pending", response_model=list[PendingCompany])
async def pending_companies(limit: int = Query(10, ge=1, le=500), session: AsyncSession = Depends(get_session)):
    """Companies with live jobs that are due for enrichment, those with the most live jobs first.

    Due means never attempted, or attempted fewer than ENRICH_MAX_ATTEMPTS times, at least
    ENRICH_RETRY_DAYS ago, and still without any of the core facts.
    """
    now = datetime.now(timezone.utc)
    live = (
        select(Job.company_id, func.count().label("live_jobs"))
        .where(Job.created_at >= now - timedelta(days=RECONCILE_WINDOW_DAYS))
        .group_by(Job.company_id)
        .subquery()
    )
    location = (
        select(Job.location)
        .where(Job.company_id == Company.id, Job.location.is_not(None), Job.location != "")
        .order_by(Job.posted_at.desc().nulls_last())
        .limit(1)
        .scalar_subquery()
    )
    still_empty = and_(
        Company.founded_year.is_(None), Company.employee_count.is_(None), Company.review_score.is_(None),
    )
    due = or_(
        Company.enrich_attempts == 0,
        and_(
            Company.enrich_attempts < ENRICH_MAX_ATTEMPTS,
            Company.enriched_at < now - timedelta(days=ENRICH_RETRY_DAYS),
            still_empty,
        ),
    )
    rows = await session.execute(
        select(Company, location.label("location"), live.c.live_jobs)
        .join(live, live.c.company_id == Company.id)
        .where(due, Company.name != "")
        .order_by(live.c.live_jobs.desc(), Company.created_at.desc())
        .limit(limit)
    )
    return [
        PendingCompany(
            name=c.name, website=c.website, linkedin_url=c.linkedin_url, industry=c.industry,
            location=loc, live_jobs=n,
        )
        for c, loc, n in rows
    ]


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(company_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="not found")
    return company
