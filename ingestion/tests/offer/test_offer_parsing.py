from datetime import date

from ingestion.offer.models import OfferExtraction
from ingestion.offer.parsing.parsing import parse, parse_with_text
from ingestion.offer.scraping.models import JobOffer


def _make_offer(description: str | None, title: str = "Senior Python Engineer", location: str = "Berlin") -> JobOffer:
    return JobOffer(
        title=title,
        company="Acme",
        location=location,
        url="https://example.com/job/1",
        source="test",
        posted_at=date(2024, 1, 15),
        description=description,
    )


DESCRIPTION = """About us
We are Acme, a company.

Responsibilities
- Build ML pipelines in Python
- Deploy models to production

Requirements
- Fluent German and English
- 5+ years of experience

Benefits
- Hybrid working, 2 days in the office
- Salary €70,000 - €90,000 per year"""


def test_parse_extracts_fields_without_llm():
    result = parse(_make_offer(DESCRIPTION))
    assert result.seniority == "senior"
    assert result.employment_type == "full-time"
    assert result.location_type == "hybrid"
    assert result.office == "berlin"
    assert result.salary_range == "€70,000 - €90,000 per year"
    assert result.text_language == "english"
    assert set(result.languages_required) == {"english", "german"}


def test_parse_summary_from_sections():
    result = parse(_make_offer(DESCRIPTION))
    assert result.summary.responsibilities == ["Build ML pipelines in Python", "Deploy models to production"]
    assert result.summary.requirements == ["Fluent German and English", "5+ years of experience"]
    assert result.summary.domain == []


def test_parse_with_text_returns_kept_text():
    _, text = parse_with_text(_make_offer(DESCRIPTION))
    assert "Build ML pipelines" in text
    assert "We are Acme" not in text


def test_parse_without_description_still_uses_title():
    result = parse(_make_offer(None, title="Junior Data Analyst (Remote)", location=""))
    assert isinstance(result, OfferExtraction)
    assert result.seniority == "junior"
    assert result.location_type == "remote"
    assert result.text_language is None
    assert result.summary.requirements == []
