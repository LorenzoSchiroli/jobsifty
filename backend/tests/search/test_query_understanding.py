import json
from unittest.mock import AsyncMock, MagicMock, patch
from backend.search.query_understanding import extract_filters
from backend.search.filters import SearchFilters


def _fake_pool(content: str | None):
    pool = MagicMock()

    async def acomplete(prompt, parse, what="LLM call", wait_up_to=0.0, *, system=None, json=True):
        return None if content is None else parse(content)

    pool.acomplete = AsyncMock(side_effect=acomplete)
    return pool


def _patched(response: dict | None):
    content = None if response is None else json.dumps(response)
    pool = _fake_pool(content)
    return pool, patch("backend.search.query_understanding.get_pool", return_value=pool)


async def test_returns_search_filters():
    pool, p = _patched({"semantic_query": "senior python remote", "seniority": "senior", "location_type": "remote"})
    with p:
        result = await extract_filters(cv_text="5yr Python", query="senior python remote")
    assert isinstance(result, SearchFilters)
    assert result.semantic_query == "senior python remote"
    assert result.seniority == "senior"
    assert result.location_type == "remote"


async def test_uses_json_mode_and_user_message():
    pool, p = _patched({"semantic_query": "engineer"})
    with p:
        await extract_filters(cv_text="cv", query="q")
    call = pool.acomplete.call_args
    assert call.args[0] == "CV:\ncv\n\nSearch query:\nq"
    assert call.kwargs["json"] is True
    assert call.kwargs["system"]


async def test_falls_back_to_query_when_semantic_query_missing():
    pool, p = _patched({})
    with p:
        result = await extract_filters(cv_text="cv", query="fallback query")
    assert result.semantic_query == "fallback query"


async def test_extract_filters_falls_back_to_raw_query():
    pool, p = _patched(None)
    with p:
        f = await extract_filters(cv_text="cv", query="python remote")
    assert f.semantic_query == "python remote" and f.seniority is None


def test_backend_pool_uses_large_groq_model_and_no_pacing(monkeypatch):
    from backend import llm as backend_llm
    backend_llm.get_pool.cache_clear()
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL_LARGE", "big")
    pool = backend_llm.get_pool()
    groq = next(p for p in pool.providers if p.name == "groq")
    assert groq.model == "big" and all(p.min_interval == 0.0 for p in pool.providers)
    backend_llm.get_pool.cache_clear()
