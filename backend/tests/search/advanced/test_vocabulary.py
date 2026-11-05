"""Brief and soft-filter values are tied to the index vocabulary (final review, Critical 1 and Important 2, 3, 9)."""
import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from backend.search.advanced.brief import Brief, BriefItem, hard_filters, merge_brief, satisfies, unmet, violations
from backend.search.advanced.observation import render_observation
from backend.search.advanced.search import SearchVariant, VariantFilters, matches_soft, soft_matches
from backend.search.advanced.tools import search_jobs, update_brief
from backend.search.filters import build_clauses
from tests.search.advanced.conftest import NOW
from tests.search.advanced.samples import candidate


def stated(field, value, text="x", kind="require") -> BriefItem:
    return BriefItem(text=text, kind=kind, source="stated", field=field, value=value)


@pytest.mark.parametrize("field,raw,expected", [
    ("location_type", "onsite", "on-site"),
    ("location_type", "On site", "on-site"),
    ("location_type", "in office", "on-site"),
    ("location_type", " Remote ", "remote"),
    ("employment_type", "fulltime", "full-time"),
    ("employment_type", "Full time", "full-time"),
    ("seniority", "Mid-level", "mid"),
    ("location_type", "remote|hybrid", ["remote", "hybrid"]),
    ("location_type", "remote or hybrid", ["remote", "hybrid"]),
    ("seniority", ["mid", "Senior"], ["mid", "senior"]),
    ("seniority", ["senior"], "senior"),  # a one-value list is that value
    ("languages_required", "en", ["english"]),
    ("languages_required", ["EN", "de", "English"], ["english", "german"]),
    ("company_country", "DE", "Germany"),
    ("company_country", "Narnia", "Narnia"),  # unknown countries are kept as written
])
def test_values_are_normalised_to_the_index_vocabulary(field, raw, expected):
    assert stated(field, raw).value == expected


def test_a_value_outside_the_vocabulary_is_refused_with_the_allowed_values():
    with pytest.raises(ValidationError, match="remote, hybrid, on-site"):
        stated("location_type", "office-ish")


def test_employee_count_is_not_a_brief_field():
    with pytest.raises(ValidationError):
        stated("employee_count", "11-50")
    assert BriefItem(text="a small company", kind="require", source="stated").field is None


def test_tool_schema_carries_the_vocabulary():
    schema = json.dumps(convert_to_openai_tool(update_brief))
    for value in ("on-site", "full-time", "principal", "english", "stage"):
        assert f'"{value}"' in schema
    assert "employee_count" not in schema
    search_schema = json.dumps(convert_to_openai_tool(search_jobs))
    assert '"on-site"' in search_schema and "employee_count" not in search_schema and "industry" not in search_schema


def test_onsite_written_by_the_model_filters_and_checks_on_site_jobs():
    brief = Brief(items=[stated("location_type", "onsite", "on-site only")])
    assert build_clauses(hard_filters(brief).model_copy(update={"strict": True})) == [
        {"term": {"location_type": "on-site"}}]
    assert violations(brief, {"location_type": "on-site"}) == []
    assert violations(brief, {"location_type": "remote"}) == ["on-site only"]


def test_iso_language_code_keeps_english_jobs():
    brief = Brief(items=[stated("languages_required", ["en"], "english")])
    clauses = build_clauses(hard_filters(brief).model_copy(update={"strict": True}))
    assert clauses == [{"terms": {"languages_required": ["english"]}}]
    assert satisfies(brief.items[0], {"languages_required": ["english"]}) is True


def test_a_list_value_is_an_or_in_the_hard_filter_and_the_checks():
    brief = Brief(items=[stated("location_type", ["remote", "hybrid"], "remote or hybrid"),
                         stated("seniority", "mid or senior", "mid or senior")])
    clauses = build_clauses(hard_filters(brief).model_copy(update={"strict": True}))
    assert {"terms": {"location_type": ["remote", "hybrid"]}} in clauses
    assert {"terms": {"seniority": ["mid", "senior"]}} in clauses
    assert violations(brief, {"location_type": "hybrid", "seniority": "mid"}) == []
    assert violations(brief, {"location_type": "on-site", "seniority": "lead"}) == ["remote or hybrid", "mid or senior"]


def test_soft_list_clause_is_boosted_terms():
    f = VariantFilters(location_type=["remote", "hybrid"]).to_search_filters("q", 72)
    assert build_clauses(f) == [{"terms": {"location_type": ["remote", "hybrid"], "boost": 2.0}}]


def test_two_stated_items_on_one_field_are_refused():
    with pytest.raises(ValidationError, match="list"):
        Brief(items=[stated("location_type", "remote", "remote"), stated("location_type", "hybrid", "hybrid")])
    # an inferred item next to a stated one is fine
    other = BriefItem(text="hybrid ok", kind="prefer", source="inferred", field="location_type", value="hybrid")
    assert len(Brief(items=[stated("location_type", "remote"), other]).items) == 2


async def test_update_brief_refuses_two_stated_items_on_one_field(run_tool):
    items = [stated("location_type", "remote", "remote").model_dump(), stated("location_type", "hybrid", "hybrid").model_dump()]
    out = await run_tool("update_brief", {"items": items})
    assert out.messages[-1].content.startswith("Refused: two stated items set location_type")
    assert "brief" not in out.state


def test_correcting_a_spelling_of_a_locked_value_is_not_a_change():
    current = Brief(items=[stated("location_type", "onsite", "on-site only")])
    proposed = Brief(items=[stated("location_type", "on-site", "on-site only")])
    assert merge_brief(current, proposed, unlock_allowed=False) == proposed


def test_variant_filters_are_normalised_and_refuse_unknown_values():
    f = VariantFilters(location_type="onsite", employment_type="full time", languages_required=["en"],
                       company_country="de")
    assert (f.location_type, f.employment_type, f.languages_required, f.company_country) == (
        "on-site", "full-time", ["english"], "Germany")
    with pytest.raises(ValidationError):
        VariantFilters(seniority="wizard")


def test_matches_soft_with_lists_and_review_score():
    c = candidate("a1f30000-0000-4000-8000-000000000001", location_type="hybrid", review_score=4.2)
    assert matches_soft(c, SearchVariant(query="x", filters=VariantFilters(location_type=["remote", "hybrid"])))
    assert not matches_soft(c, SearchVariant(query="x", filters=VariantFilters(location_type="remote")))
    assert matches_soft(c, SearchVariant(query="x", filters=VariantFilters(min_review_score=4)))
    assert not matches_soft(c, SearchVariant(query="x", filters=VariantFilters(min_review_score=4.5)))


def test_stated_country_matches_iso_coded_companies():
    item = stated("company_country", "Germany", "in Germany")
    assert satisfies(item, {"company_country": "DE"}) is True
    assert satisfies(item, {"company_country": "Germany"}) is True
    assert satisfies(item, {"company_country": "FR"}) is False
    assert violations(Brief(items=[item]), {"company_country": "DE"}) == []


def test_unmet_counts_unknown_as_unmet_but_only_for_carried_fields():
    brief = Brief(items=[stated("location_type", "remote", "remote only"),
                         stated("min_review_score", 4, "good reviews")])
    assert unmet(brief, {"location_type": "remote"}) == []  # review score not carried: not checked
    assert unmet(brief, {"location_type": None}) == ["remote only"]
    assert unmet(brief, {"location_type": "remote", "min_review_score": None}) == ["good reviews"]
    assert violations(brief, {"location_type": None}) == []  # a contradiction needs a known value


def test_observation_counts_english_as_english():
    window = [candidate(f"{i:04x}0000-0000-4000-8000-000000000000", languages=langs)
              for i, langs in enumerate([["english"], ["english", "german"], ["french"]], 1)]
    obs = render_observation(1, SearchVariant(query="ml"), None, window, {}, pool_size=3, now=NOW)
    line = next(l for l in obs.splitlines() if "other than English" in l)
    assert "EN" not in line and "DE 1" in line and "FR 1" in line
    assert "EN/DE" in obs  # job lines show language codes


def test_no_soft_filters_reports_the_jobs_returned():
    window = [candidate(f"{i:04x}0000-0000-4000-8000-000000000000") for i in range(1, 4)]
    plain = SearchVariant(query="ml")
    assert soft_matches(plain, window) == 3
    assert "no soft filters: 3 jobs returned" in render_observation(1, plain, None, window, {}, 3, NOW)
    filtered = SearchVariant(query="ml", filters=VariantFilters(seniority="junior"))
    assert soft_matches(filtered, window) == 0
    assert "matching all soft filters: 0" in render_observation(1, filtered, None, window, {}, 3, NOW)
