import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Company, Job, Outbox
from backend.schemas import JobListItem, JobListResponse, JobRequest, JobResponse
from backend.routers.companies import _normalize, _is_empty

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Fields ingestion derives deterministically from the description. A post that
# carries a description overwrites them outright (even with empty values), so
# re-posting a job applies the current rules; a post without one only fills
# fields that are still empty, so a failed description fetch can't wipe them.
_DERIVED_FIELDS = (
    "employment_type", "location_type", "office", "seniority", "salary_range",
    "languages_required", "text_language", "chunks",
)


@router.post("/", response_model=JobResponse, status_code=201)
async def upsert_job(body: JobRequest, response: Response, session: AsyncSession = Depends(get_session)):
    normalized_company = _normalize(body.company_name)
    company_result = await session.execute(select(Company).where(Company.name == normalized_company))
    company = company_result.scalar_one_or_none()
    if company is None:
        try:
            company = Company(name=normalized_company)
            session.add(company)
            await session.flush()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(Company).where(Company.name == normalized_company))
            company = result.scalar_one()

    job_result = await session.execute(select(Job).where(Job.url == body.url))
    job = job_result.scalar_one_or_none()

    job_data = body.model_dump(exclude={"company_name"})

    if job is None:
        job = Job(company_id=company.id, **job_data)
        session.add(job)
        response.status_code = 201
    else:
        overwrite = _DERIVED_FIELDS if body.description and body.description.strip() else ()
        for field, value in job_data.items():
            if field in overwrite:
                setattr(job, field, value)
            elif field != "url" and not _is_empty(value) and _is_empty(getattr(job, field)):
                setattr(job, field, value)
        response.status_code = 200

    await session.flush()

    session.add(Outbox(
        event_type="job_upserted",
        entity_id=job.id,
    ))

    await session.commit()
    await session.refresh(job)
    return job


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    after: uuid.UUID | None = None,
    limit: int = Query(500, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .order_by(Job.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(Job.id > after)
    rows = (await session.execute(stmt)).all()
    items = [
        JobListItem(
            id=job.id, url=job.url, title=job.title, company_name=company_name,
            location=job.location, source=job.source, posted_at=job.posted_at,
            description=job.description,
        )
        for job, company_name in rows
    ]
    next_after = items[-1].id if len(items) == limit else None
    return JobListResponse(items=items, next_after=next_after)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="not found")
    return job
