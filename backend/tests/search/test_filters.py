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
