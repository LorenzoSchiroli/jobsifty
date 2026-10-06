from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, field_validator, model_validator

EmploymentType = Literal["full-time", "part-time", "contract", "internship", "stage", "freelance"]
LocationType = Literal["on-site", "remote", "hybrid"]
Seniority = Literal["junior", "mid", "senior", "lead", "principal", "staff", "director"]


class OfferExtraction(BaseModel):
    employment_type: EmploymentType | None = None
    location_type: LocationType | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_string_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: None if v == "null" else v for k, v in data.items()}
        return data

    office: str | None = None
    seniority: Seniority | None = None
    salary_range: str | None = None
    languages_required: list[str] = []
    text_language: str | None = None

    @field_validator("office", mode="before")
    @classmethod
    def reject_sentence_office(cls, v: str | None) -> str | None:
        if v and len(v.split()) > 6:
            return None
        return v

    @field_validator("languages_required", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: list[str] | None) -> list[str]:
        return v or []


class EnrichedOffer(BaseModel):
    title: str
    company: str
    location: str
    url: str
    source: str
    posted_at: datetime | None
    description: str | None = None
    employment_type: str | None = None
    location_type: str | None = None
    office: str | None = None
    seniority: str | None = None
    salary_range: str | None = None
    languages_required: list[str] = []
    text_language: str | None = None
    chunks: list[dict] = []
