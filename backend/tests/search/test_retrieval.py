from unittest.mock import AsyncMock
from backend.search.retrieval import build_hybrid_query, hybrid_retrieve
from backend.search.filters import SearchFilters
from backend.opensearch_client import PIPELINE_NAME


def test_build_hybrid_query_has_two_legs():
    q = build_hybrid_query("python", [0.1] * 384, SearchFilters(semantic_query="python"))
    legs = q["query"]["hybrid"]["queries"]
    assert len(legs) == 2
    assert "match" in legs[0]["bool"]["must"]
    nested = legs[1]["bool"]["must"]["nested"]
    assert nested["path"] == "chunks"
    assert nested["score_mode"] == "max"
    assert nested["query"]["knn"]["chunks.embedding"]["vector"] == [0.1] * 384
    assert "inner_hits" not in nested  # hybrid queries drop them; the reranker picks the chunk
    assert q["size"] == 20


def test_build_hybrid_query_soft_boosts_stay_outside_nested():
    f = SearchFilters(semantic_query="x", seniority="senior", max_age_hours=None)
    q = build_hybrid_query("x", [0.0] * 384, f)
    knn_leg = q["query"]["hybrid"]["queries"][1]
    assert knn_leg["bool"]["should"]
    assert "should" not in knn_leg["bool"]["must"]["nested"]


def test_build_hybrid_query_soft_applies_boosts_to_both_legs():
    f = SearchFilters(semantic_query="x", seniority="senior", is_consulting=False, max_age_hours=None)
    q = build_hybrid_query("x", [0.0] * 384, f)
    for leg in q["query"]["hybrid"]["queries"]:
        assert len(leg["bool"]["should"]) == 2
    assert "post_filter" not in q


def test_build_hybrid_query_strict_uses_post_filter():
    f = SearchFilters(semantic_query="x", is_startup=True, strict=True)
    q = build_hybrid_query("x", [0.0] * 384, f)
    assert "post_filter" in q
    assert {"term": {"is_startup": True}} in q["post_filter"]["bool"]["filter"]
    for leg in q["query"]["hybrid"]["queries"]:
        assert "filter" not in leg["bool"]
        assert "should" not in leg["bool"]


def test_build_hybrid_query_strict_uses_large_prefetch():
    f = SearchFilters(semantic_query="x", is_startup=True, strict=True)
    q = build_hybrid_query("x", [0.0] * 384, f)
    assert q["size"] == 200
    assert "_source" not in q  # chunk vectors are needed by the reranker
    assert "nested" in q["query"]["hybrid"]["queries"][1]["bool"]["must"]


def test_build_hybrid_query_strict_no_post_filter_when_no_clauses():
    q = build_hybrid_query("x", [0.0] * 384, SearchFilters(semantic_query="x", strict=True, max_age_hours=None))
    assert "post_filter" not in q


def test_build_hybrid_query_soft_country_boosts_both_legs():
    f = SearchFilters(semantic_query="germany", country="Germany", max_age_hours=None)
    q = build_hybrid_query("germany", [0.0] * 384, f)
    for leg in q["query"]["hybrid"]["queries"]:
        assert {"terms": {"country": ["Germany", "DE"], "boost": 2.0}} in leg["bool"]["should"]
    assert "post_filter" not in q


def test_build_hybrid_query_strict_country_uses_post_filter():
    f = SearchFilters(semantic_query="germany", country="Germany", strict=True, max_age_hours=None)
    q = build_hybrid_query("germany", [0.0] * 384, f)
    assert {"terms": {"country": ["Germany", "DE"]}} in q["post_filter"]["bool"]["filter"]
    for leg in q["query"]["hybrid"]["queries"]:
        assert "should" not in leg["bool"]


async def test_hybrid_retrieve_uses_pipeline():
    mock_os = AsyncMock()
    mock_os.search.return_value = {"hits": {"hits": [{"_source": {"job_id": "abc"}}]}}
    result = await hybrid_retrieve(mock_os, [0.1] * 384, SearchFilters(semantic_query="python"))
    kwargs = mock_os.search.call_args.kwargs
    assert kwargs["params"]["search_pipeline"] == PIPELINE_NAME
    assert result == [{"_source": {"job_id": "abc"}}]
