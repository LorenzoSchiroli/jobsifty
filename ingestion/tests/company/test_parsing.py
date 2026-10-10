from unittest.mock import patch

import pytest

from ingestion.company.models import CompanyExtraction
from ingestion.company.parsing.parsing import parse

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


def test_parse_without_llm_is_deterministic():
    with patch("ingestion.company.parsing.parsing.extract_with_llm") as llm:
        result = parse("Acme", "Zürich, Schweiz", _TEXT, llm=None)

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
        result = parse("Acme", "Zürich, Schweiz", _TEXT, llm=object())

    llm.assert_called_once()
    assert result.founded_year == 2019
    assert result.industry == "management consulting"
    assert result.topic == "Acme advises banks on risk."
    assert result.financial_health_score == 4


def test_parse_survives_llm_failure():
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=None):
        result = parse("Acme", "Zürich, Schweiz", _TEXT, llm=object())

    assert result.founded_year == 2019
    assert result.topic == "Swiss consulting firm"
    assert result.financial_health_score is None


def test_parse_handles_empty_input():
    result = parse("Acme", "", {}, llm=None)
    assert result == CompanyExtraction()


def test_parse_uses_job_board_facts_and_search_answer_without_llm():
    text = {
        "known": {"industry": "Drones", "description": "Flyability builds drones."},
        "search_answer": "Flyability, a Swiss company founded in 2014, has 120 employees.",
    }
    result = parse("Flyability", "Paudex, Vaud, Switzerland", text, llm=None)

    assert result.industry == "Drones"
    assert result.founded_year == 2014
    assert result.employee_count == "120"
    assert result.description == "Flyability, a Swiss company founded in 2014, has 120 employees."


def test_parse_sends_search_results_to_llm_and_skips_answer_rules():
    text = {
        "search_answer": "Acme was founded in 1990.",
        "search_snippets": ["Acme makes anvils."],
        "known": {"description": "Anvils since forever"},
    }
    llm_result = CompanyExtraction(founded_year=1991, topic="Acme makes anvils.")
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=llm_result) as llm:
        result = parse("Acme", "Berlin", text, llm=object())

    sources = llm.call_args.args[3]
    assert "Summary: Acme was founded in 1990." in sources
    assert "- Acme makes anvils." in sources
    assert "Anvils since forever" in sources
    assert result.founded_year == 1991


def test_parse_falls_back_to_answer_rules_when_llm_fails():
    text = {"search_answer": "Acme was founded in 1990 and has 45 employees."}
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=None):
        result = parse("Acme", "Berlin", text, llm=object())

    assert result.founded_year == 1990
    assert result.employee_count == "45"


def test_llm_headquarters_beats_the_job_location():
    llm_result = CompanyExtraction(country="United States")
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=llm_result):
        result = parse("Alignerr", "Edinburgh, Scotland, UK", {"search_answer": "Alignerr ..."}, llm=object())
    assert result.country == "United States"


def test_job_location_is_the_last_resort_for_country():
    with patch("ingestion.company.parsing.parsing.extract_with_llm", return_value=None):
        result = parse("Acme", "Berlin, Berlin, Germany", {}, llm=object())
    assert result.country == "Germany"
