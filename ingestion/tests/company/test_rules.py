from datetime import date

import pytest

from ingestion.company.parsing.rules import (
    country_from_location,
    detect_consulting,
    detect_startup,
    parse_answer,
    parse_employee_count,
    parse_glassdoor,
)


@pytest.mark.parametrize("snippet, score, count", [
    ("Acme has an employee rating of 4.1 out of 5 stars, based on 1,234 company reviews", 4.1, 1234),
    ("Acme is rated 3.8 by employees.", 3.8, None),
    ("Bewertung 4,1 ★ aus 87 Bewertungen", 4.1, None),
    ("★ 4.5 · 1.2K reviews", 4.5, 1200),
    ("Great culture. 4.2 stars based on 35 reviews.", 4.2, 35),
    ("3.9/5 from 512 employee reviews", 3.9, 512),
])
def test_parse_glassdoor(snippet, score, count):
    result = parse_glassdoor([snippet])
    assert result.get("review_score") == score
    assert result.get("review_count") == count


def test_parse_glassdoor_combines_snippets_and_ignores_noise():
    result = parse_glassdoor(["Salaries for 12 jobs", "Rated 4.0 out of 5", "based on 99 reviews"])
    assert result == {"review_score": 4.0, "review_count": 99}


def test_parse_glassdoor_rejects_out_of_range_and_empty():
    assert parse_glassdoor(["version 7.5 stars"]) == {}
    assert parse_glassdoor(None) == {}


@pytest.mark.parametrize("texts", [
    ("Accenture", "IT consulting"),
    ("Roland Berger Unternehmensberatung",),
    ("Capgemini", None, "société de conseil"),
    ("Deloitte", "advisory services"),
    ("Everis", "consultoría tecnológica"),
])
def test_detect_consulting_true(texts):
    assert detect_consulting(*texts) is True


def test_detect_consulting_never_false():
    assert detect_consulting("Datadog", "software", None) is None


@pytest.mark.parametrize("value, expected", [
    ("51-200", 200), ("1,234", 1234), ("10K+", 10000), ("320000", 320000), (45, 45), (None, None), ("n/a", None),
])
def test_parse_employee_count(value, expected):
    assert parse_employee_count(value) == expected


def test_detect_startup():
    today = date(2026, 10, 4)
    assert detect_startup(2021, "11-50", today) is True
    assert detect_startup(2021, "5000", today) is None
    assert detect_startup(1990, "20", today) is None
    assert detect_startup(None, "20", today) is None
    assert detect_startup(2021, None, today) is None


@pytest.mark.parametrize("location, expected", [
    ("Berlin, Germany", "Germany"),
    ("München, Deutschland", "Germany"),
    ("Munich, BY, DE", "Germany"),
    ("Milano, Lombardia, Italia", "Italy"),
    ("Zürich, Schweiz", "Switzerland"),
    ("London, England, United Kingdom", "United Kingdom"),
    ("Germany", "Germany"),
    ("San Francisco, CA", None),
    ("Wilmington, DE", None),
    ("Remote", None),
    ("", None),
    (None, None),
])
def test_country_from_location(location, expected):
    assert country_from_location(location) == expected


@pytest.mark.parametrize("answer, expected", [
    ("Menhir AI is an AI company founded in 2019, headquartered in Pozuelo de Alarcón, Spain, with 13 employees.",
     {"founded_year": 2019, "employee_count": "13", "country": "Spain"}),
    ("Darktrace was founded in 2013 and has 2,400 employees; it is based in Cambridge, United Kingdom.",
     {"founded_year": 2013, "employee_count": "2,400", "country": "United Kingdom"}),
    # a bare year in front of "employees" is not a head count
    ("Instaffo is headquartered in Heidelberg, Germany. In 2014 employees moved offices.", {"country": "Germany"}),
    # a founding date in the future is a misread
    ("MBDA, founded in 2031, makes missiles.", {}),
    ("The sources do not provide specific information.", {}),
    (None, {}),
])
def test_parse_answer(answer, expected):
    assert parse_answer(answer, today=date(2026, 10, 8)) == expected
