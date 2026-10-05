from unittest.mock import patch

import pytest

from ingestion.company.models import CompanyExtraction
from ingestion.company.parsing.extractor import reset_breaker
from ingestion.company.parsing.parsing import parse, parse_financial

_TEXT = {
    "website_url": "https://acme.com",
    "review_snippets": ["Acme is rated 4.1 out of 5, based on 230 reviews"],
    "wikidata": {
        "founded_year": 2019,
        "employee_count": "120",
        "industry": "management consulting",
        "description": "Swiss consulting firm",
    },
}


@pytest.fixture(autouse=True)
def _reset():
    reset_breaker()
    yield
    reset_breaker()


def test_parse_without_client_is_deterministic():
    with patch("ingestion.company.parsing.parsing.extract_with_llm") as llm:
        result = parse("Acme", "Zürich, Schweiz", _TEXT, client=None)

    llm.assert_not_called()
    assert result.website == "https://acme.com"
    assert result.founded_year == 2019
    assert result.employee_count == "120"
    assert result.industry == "management consulting"
    assert result.review_score == 4.1
    assert result.review_count == 230
    assert result.is_consulting is True
    assert result.is_startup is True
    assert result.country == "Switzerland"
    assert result.topic == "Swiss consulting firm"


def test_parse_with_llm_only_fills_missing_fields():
    llm_result = CompanyExtraction(
        founded_year=1999,                # already known from Wikidata → ignored
        industry="software",              # already known → ignored
        topic="Acme advises banks on risk.",
        financial_health_score=4,
        financial_health_rationale="Profitable.",
    )
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=llm_result) as llm:
        result = parse("Acme", "Zürich, Schweiz", _TEXT, client=object())

    llm.assert_called_once()
    assert result.founded_year == 2019
    assert result.industry == "management consulting"
    assert result.topic == "Acme advises banks on risk."
    assert result.financial_health_score == 4


def test_parse_survives_llm_failure():
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=None):
        result = parse("Acme", "Zürich, Schweiz", _TEXT, client=object())

    assert result.founded_year == 2019
    assert result.topic == "Swiss consulting firm"
    assert result.financial_health_score is None


def test_parse_handles_empty_input():
    result = parse("Acme", "", {}, client=None)
    assert result == CompanyExtraction()


def test_parse_financial_skips_without_client():
    info = CompanyExtraction(financial_health_score=3)
    with patch("ingestion.company.parsing.parsing.extract_with_llm") as llm:
        assert parse_financial("Acme", "", info, {}, None) is info
    llm.assert_not_called()


def test_parse_financial_keeps_info_on_llm_failure():
    info = CompanyExtraction(financial_health_score=3)
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=None):
        assert parse_financial("Acme", "", info, {"financial_snippets": ["x"]}, object()) is info
