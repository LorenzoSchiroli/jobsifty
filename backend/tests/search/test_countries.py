import pytest

from backend.search.advanced.brief import Brief, BriefItem, merge_brief
from backend.search.advanced.vocabulary import normalise_country
from backend.search.countries import canonical_country, country_values, same_country
from backend.search.filters import SearchFilters, build_clauses

CASES = [
    ("UK", "United Kingdom", "GB"),
    ("USA", "United States", "US"),
    ("England", "United Kingdom", "GB"),
    ("Deutschland", "Germany", "DE"),
    ("the Netherlands", "Netherlands", "NL"),
    ("Czechia", "Czech Republic", "CZ"),
]


@pytest.mark.parametrize("alias,name,iso", CASES)
def test_alias_normalises_and_filters_on_name_and_iso(alias, name, iso):
    assert normalise_country(alias) == name
    assert country_values(alias) == [name, iso]
    clauses = build_clauses(SearchFilters(semantic_query="x", country=alias, strict=True))
    assert {"terms": {"country": [name, iso]}} in clauses


def test_correcting_a_locked_alias_to_the_canonical_name_is_accepted():
    uk = BriefItem(text="UK based", kind="require", source="stated", field="company_country", value="UK")
    full = BriefItem(text="UK based", kind="require", source="stated", field="company_country",
                     value="United Kingdom")
    assert merge_brief(Brief(items=[uk]), Brief(items=[full]), unlock_allowed=False).items == [full]


def test_us_state_codes_are_not_countries_in_a_job_location():
    assert canonical_country("CA", in_location=True) is None
    assert canonical_country("CA") == "Canada"
    assert same_country("UK", "GB")
