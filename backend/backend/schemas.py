import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


class CompanyFields(BaseModel):
    name: str
    website: str | None = None
    linkedin_url: str | None = None
    topic: str | None = None
    country: str | None = None
    founded_year: int | None = None
    employee_count: str | None = None
    industry: str | None = None
    is_consulting: bool | None = None
    is_startup: bool | None = None
    review_score: float | None = None
    review_count: int | None = None
    description: str | None = None
    financial_health_score: int | None = None
    financial_health_rationale: str | None = None
    registration_numbers: dict[str, str] | None = None


class CompanyRequest(CompanyFields):
    # Set by ingestion after an enrichment attempt, found data or not: stamps
    # enriched_at and counts the attempt, so empty companies are retried later.
    enrichment_attempted: bool = False


class CompanyResponse(CompanyFields):
    id: uuid.UUID
    enriched_at: datetime | None = None
    enrich_attempts: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PendingCompany(BaseModel):
    """A company due for enrichment, with what ingestion needs to search for it."""
    name: str
    website: str | None = None
    linkedin_url: str | None = None
    industry: str | None = None
    location: str | None = None
    live_jobs: int


class JobChunk(BaseModel):
    text: str
    embedding: list[float]


class JobRequest(BaseModel):
    url: str
    title: str
    company_name: str
    location: str | None = None
    source: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    employment_type: str | None = None
    location_type: str | None = None
    office: str | None = None
    seniority: str | None = None
    salary_range: str | None = None
    languages_required: list[str] = []
    text_language: str | None = None
    chunks: list[JobChunk] | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    url: str
    company_id: uuid.UUID
    title: str
    location: str | None = None
    source: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    employment_type: str | None = None
    location_type: str | None = None
    office: str | None = None
    seniority: str | None = None
    salary_range: str | None = None
    languages_required: list[str] = []
    text_language: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("languages_required", mode="before")
    @classmethod
    def coerce_null(cls, v: list[str] | None) -> list[str]:
        return v or []


class JobListItem(BaseModel):
    id: uuid.UUID
    url: str
    title: str
    company_name: str
    location: str | None = None
    source: str | None = None
    posted_at: datetime | None = None
    description: str | None = None


class JobListResponse(BaseModel):
    items: list[JobListItem]
    next_after: uuid.UUID | None = None


class CompanyInSearch(BaseModel):
    name: str
    is_consulting: bool | None = None
    is_startup: bool | None = None
    review_score: float | None = None
    financial_health_score: int | None = None
    industry: str | None = None
    country: str | None = None

    model_config = {"from_attributes": True}


class JobSearchResponse(JobResponse):
    company: CompanyInSearch
