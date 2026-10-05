import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


class CompanyRequest(BaseModel):
    name: str
    website: str | None = None
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


class CompanyResponse(CompanyRequest):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


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
    summary: dict | None = None
    embedding: list[float] | None = None
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
