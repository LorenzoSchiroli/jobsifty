from backend.search.filters import SearchFilters, build_clauses


def test_only_semantic_query_required():
    f = SearchFilters(semantic_query="python engineer")
    assert f.seniority is None


def test_build_clauses_empty_when_all_none():
    assert build_clauses(SearchFilters(semantic_query="x", max_age_hours=None)) == []


def test_build_clauses_soft_term_bool():
    result = build_clauses(SearchFilters(semantic_query="x", is_consulting=True))
    assert {"term": {"is_consulting": {"value": True, "boost": 2.0}}} in result


def test_build_clauses_soft_term_string():
    result = build_clauses(SearchFilters(semantic_query="x", seniority="senior"))
    assert {"term": {"seniority": {"value": "senior", "boost": 2.0}}} in result


def test_build_clauses_soft_range_review_score():
    result = build_clauses(SearchFilters(semantic_query="x", min_review_score=4.0))
    assert {"range": {"review_score": {"gte": 4.0, "boost": 2.0}}} in result


def test_build_clauses_soft_range_financial_health():
    result = build_clauses(SearchFilters(semantic_query="x", min_financial_health_score=3))
    assert {"range": {"financial_health_score": {"gte": 3, "boost": 2.0}}} in result


def test_build_clauses_soft_terms_languages():
    result = build_clauses(SearchFilters(semantic_query="x", languages_required=["Python", "Go"]))
    assert {"terms": {"languages_required": ["python", "go"], "boost": 2.0}} in result


def test_build_clauses_soft_term_country():
    result = build_clauses(SearchFilters(semantic_query="x", country="Germany"))
    assert {"terms": {"country": ["Germany", "DE"], "boost": 2.0}} in result


def test_build_clauses_strict_term_country():
    result = build_clauses(SearchFilters(semantic_query="x", country="Germany", strict=True))
    assert {"terms": {"country": ["Germany", "DE"]}} in result


def test_build_clauses_country_iso_code_expands_to_name():
    result = build_clauses(SearchFilters(semantic_query="x", country="DE", strict=True))
    assert {"terms": {"country": ["Germany", "DE"]}} in result


def test_build_clauses_country_unknown_stays_single_term():
    result = build_clauses(SearchFilters(semantic_query="x", country="Atlantis", strict=True))
    assert {"term": {"country": "Atlantis"}} in result


def test_build_clauses_strict_term_bool():
    result = build_clauses(SearchFilters(semantic_query="x", is_startup=True, strict=True))
    assert {"term": {"is_startup": True}} in result


def test_build_clauses_strict_range():
    result = build_clauses(SearchFilters(semantic_query="x", min_review_score=4.0, strict=True))
    assert {"range": {"review_score": {"gte": 4.0}}} in result


def test_build_clauses_strict_terms_languages():
    result = build_clauses(SearchFilters(semantic_query="x", languages_required=["English"], strict=True))
    assert {"terms": {"languages_required": ["english"]}} in result


def test_build_clauses_multiple():
    result = build_clauses(SearchFilters(semantic_query="x", seniority="senior", is_startup=True, min_review_score=3.5, max_age_hours=None))
    assert len(result) == 3


def test_build_clauses_country_combined_with_other_filters():
    result = build_clauses(SearchFilters(
        semantic_query="x", country="Germany", seniority="senior", max_age_hours=None,
    ))
    assert {"terms": {"country": ["Germany", "DE"], "boost": 2.0}} in result
    assert {"term": {"seniority": {"value": "senior", "boost": 2.0}}} in result
    assert len(result) == 2


def test_age_window_is_not_a_soft_clause():
    # the window is a hard filter applied by retrieval, never a boost
    assert build_clauses(SearchFilters(semantic_query="x", max_age_hours=24)) == []


def test_describe_lists_soft_filters_as_preferred_and_the_window_as_required():
    from backend.search.filters import describe

    f = SearchFilters(semantic_query="ml", seniority=["mid", "senior"], is_consulting=False, country="DE",
                      languages_required=["german"], max_age_hours=168)
    out = describe(f)
    assert out.required == ["posted in the last 7 days"]
    assert out.preferred == ["mid or senior", "German required", "company in Germany", "no consulting"]


def test_describe_puts_everything_in_required_when_strict_and_skips_what_is_not_filtered():
    from backend.search.filters import describe

    f = SearchFilters(semantic_query="ml", location_type="remote|hybrid", employment_type="full-time",
                      industry="fintech", strict=True, max_age_hours=None)
    out = describe(f)
    assert (out.required, out.preferred) == (["full-time"], [])  # "a|b" and industry filter nothing


def test_build_clauses_places_match_any_city_or_country():
    soft = build_clauses(SearchFilters(semantic_query="x", places=["München", "Austria"]))
    assert soft == [{"bool": {"should": [{"terms": {"job_cities": ["munich"]}},
                                         {"terms": {"job_countries": ["Austria"]}}],
                              "minimum_should_match": 1, "boost": 2.0}}]
    strict = build_clauses(SearchFilters(semantic_query="x", places=["Munich"], strict=True))
    assert strict == [{"bool": {"should": [{"terms": {"job_cities": ["munich"]}}], "minimum_should_match": 1}}]


def test_describe_words_places_as_in():
    from backend.search.filters import describe

    assert describe(SearchFilters(semantic_query="x", places=["Munich", "Austria", "Switzerland"])).preferred == [
        "in Munich, Austria or Switzerland"]
