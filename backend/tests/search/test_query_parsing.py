import pytest
from backend.search.query_parsing import parse_query


def test_plain_query_passthrough():
    f = parse_query("machine learning engineer")
    assert f.semantic_query == "machine learning engineer"
    assert f.seniority is None
    assert f.location_type is None
    assert f.max_age_hours == 72  # default preserved


@pytest.mark.parametrize("query,field,value", [
    ("remote python dev", "location_type", "remote"),
    ("wfh python dev", "location_type", "remote"),
    ("work from home python dev", "location_type", "remote"),
    ("hybrid python dev", "location_type", "hybrid"),
    ("on-site python dev", "location_type", "on-site"),
    ("on site python dev", "location_type", "on-site"),
    ("senior python dev", "seniority", "senior"),
    ("sr. python dev", "seniority", "senior"),
    ("junior python dev", "seniority", "junior"),
    ("jr python dev", "seniority", "junior"),
    ("entry-level python dev", "seniority", "junior"),
    ("mid-level python dev", "seniority", "mid"),
    ("full-time python dev", "employment_type", "full-time"),
    ("part time python dev", "employment_type", "part-time"),
    ("internship python", "employment_type", "internship"),
    ("freelance python dev", "employment_type", "freelance"),
])
def test_enum_filters(query, field, value):
    assert getattr(parse_query(query), field) == value


def test_strict_flag():
    assert parse_query("python dev strictly").strict is True
    assert parse_query("python dev").strict is False


@pytest.mark.parametrize("query,hours", [
    ("python jobs last 3 days", 72),
    ("python jobs past 2 hours", 2),
    ("python jobs last three weeks", 504),
    ("python jobs within 48 hours", 48),
    ("python jobs today", 24),
    ("python jobs yesterday", 48),
    ("python jobs this week", 168),
    ("machine learning engineer, last week", 168),
    ("python jobs in the past day", 24),
    ("python jobs last month", 720),
])
def test_time_window(query, hours):
    assert parse_query(query).max_age_hours == hours


def test_a_window_without_a_number_leaves_the_semantic_query():
    assert parse_query("machine learning engineer, last week").semantic_query.strip(" ,") == "machine learning engineer"


def test_startup_positive_and_negative():
    assert parse_query("python dev at a startup").is_startup is True
    assert parse_query("python dev no startup").is_startup is False
    assert parse_query("python dev").is_startup is None


def test_consulting_positive_and_negative():
    assert parse_query("python consulting role").is_consulting is True
    assert parse_query("python dev without consulting").is_consulting is False


def test_hyphenated_negation():
    assert parse_query("python dev non-startup").is_startup is False
    assert parse_query("python dev non-consulting").is_consulting is False


def test_languages():
    assert parse_query("dev fluent in german").languages_required == ["German"]
    assert parse_query("english-speaking dev").languages_required == ["English"]
    assert set(parse_query("dev in english and german").languages_required) == {"English", "German"}


def test_country_is_where_the_job_is():
    f_de = parse_query("python jobs in germany")
    assert f_de.places == ["Germany"] and f_de.country is None  # the job's country, not the company's
    # the place word must remain in semantic_query so hybrid search still uses it
    assert "germany" in f_de.semantic_query
    assert parse_query("python jobs in the united kingdom").places == ["United Kingdom"]


@pytest.mark.parametrize("query,places", [
    ("ml engineer in Munich", ["Munich"]),
    ("ml engineer in München or Austria", ["Munich", "Austria"]),
    ("data jobs in frankfurt am main", ["Frankfurt"]),  # one city, not Frankfurt plus a stray word
    ("jobs in luxembourg", ["Luxembourg"]),  # a city that is also a country is the country
])
def test_cities_and_several_places(query, places):
    assert parse_query(query).places == places


def test_company_country_only_when_asked_for():
    f = parse_query("nlp engineer at a company based in Germany, in Munich")
    assert (f.country, f.places) == ("Germany", ["Munich"])
    assert parse_query("ml jobs, companies headquartered in the Netherlands").country == "Netherlands"


@pytest.mark.parametrize("query,name", [
    ("python jobs in UK", "United Kingdom"),
    ("ml jobs in Deutschland", "Germany"),
    ("data jobs in the Netherlands", "Netherlands"),
])
def test_country_aliases(query, name):
    assert parse_query(query).places == [name]


def test_iso_codes_are_not_countries_in_free_text():
    assert parse_query("it support jobs, de-escalation").places is None


def test_country_case_insensitive():
    assert parse_query("Python Jobs In GERMANY").places == ["Germany"]


def test_country_absent_stays_none():
    assert parse_query("python developer role").places is None


def test_country_not_confused_with_similarly_spelled_language():
    # "germany" contains "german" as a substring — must not also set
    # languages_required just because the country name embeds a language name.
    f = parse_query("python jobs in germany")
    assert f.languages_required is None


def test_country_combined_with_other_filters():
    f = parse_query("senior remote python developer in germany, last 3 days")
    assert f.places == ["Germany"]
    assert f.seniority == "senior"
    assert f.location_type == "remote"
    assert f.max_age_hours == 72


def test_no_false_negation_on_unrelated_word():
    # "casino" ends in "no" — must not falsely negate is_startup
    assert parse_query("casino startup jobs").is_startup is True


def test_numeric_thresholds():
    assert parse_query("companies with financial health above 7").min_financial_health_score == 7
    assert parse_query("review score at least 4.5 please").min_review_score == 4.5


def test_multi_filter_and_strip():
    f = parse_query("senior remote python developer at a startup, last 3 days, strictly")
    assert f.seniority == "senior"
    assert f.location_type == "remote"
    assert f.is_startup is True
    assert f.max_age_hours == 72
    assert f.strict is True
    # control tokens are stripped from the content query; incidental filler
    # words ("at a") may remain — we only assert the content survives and the
    # control keywords are gone.
    assert "python developer" in f.semantic_query
    for gone in ("senior", "remote", "startup", "strictly", "last 3 days"):
        assert gone not in f.semantic_query


def test_conservative_strip_keeps_ambiguous_words():
    # 'contract' is an ambiguous content noun -> filter set, word kept
    f = parse_query("contract law positions")
    assert f.employment_type == "contract"
    assert "contract law" in f.semantic_query


def test_word_boundary_no_false_trigger():
    # 'remotely' must NOT trigger location_type=remote
    assert parse_query("delivered work remotely sometimes").location_type is None


def test_empty_after_strip_falls_back_to_raw():
    f = parse_query("remote")
    assert f.semantic_query == "remote"
