import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _hit(job_id, title):
    return {"_source": {"job_id": job_id, "title": title}}


@pytest.mark.asyncio
async def test_node_search_retrieves_and_reranks():
    from backend.search.advanced import nodes
    biencoder = MagicMock()
    enc = MagicMock(); enc.tolist.return_value = [0.0] * 384
    biencoder.encode.return_value = enc
    reranker = MagicMock()
    os_client = AsyncMock()
    groq_client = MagicMock()

    state = {"query": "ml engineer", "cv_text": "cv", "preference_memory": "",
             "clarify_answers": ["yes"], "refined_query": None, "refined_once": False}

    fake_filters = MagicMock(); fake_filters.semantic_query = "ml engineer"
    with patch("backend.search.advanced.nodes.extract_filters", new=AsyncMock(return_value=fake_filters)), \
         patch("backend.search.advanced.nodes.hybrid_retrieve", new=AsyncMock(return_value=[_hit("j1", "a")])), \
         patch("backend.search.advanced.nodes.rerank", return_value=[_hit("j1", "a")]):
        out = await nodes.node_search(state, biencoder=biencoder, reranker=reranker,
                                      os_client=os_client, groq_client=groq_client)
    assert out["hits"][0]["_source"]["job_id"] == "j1"


@pytest.mark.asyncio
async def test_node_search_uses_refined_query_when_present():
    from backend.search.advanced import nodes
    biencoder = MagicMock()
    enc = MagicMock(); enc.tolist.return_value = [0.0] * 384
    biencoder.encode.return_value = enc
    state = {"query": "ml", "cv_text": "cv", "preference_memory": "",
             "clarify_answers": [], "refined_query": "senior ml pytorch", "refined_once": True}
    captured = {}
    async def fake_extract(client, cv, q):
        captured["q"] = q
        f = MagicMock(); f.semantic_query = q; return f
    with patch("backend.search.advanced.nodes.extract_filters", new=fake_extract), \
         patch("backend.search.advanced.nodes.hybrid_retrieve", new=AsyncMock(return_value=[])), \
         patch("backend.search.advanced.nodes.rerank", return_value=[]):
        await nodes.node_search(state, biencoder=biencoder, reranker=MagicMock(),
                                os_client=AsyncMock(), groq_client=MagicMock())
    assert "senior ml pytorch" in captured["q"]


@pytest.mark.asyncio
async def test_node_critique_sets_refined_once_guard():
    from backend.search.advanced import nodes
    state = {"query": "ml", "hits": [], "refined_once": False}
    with patch("backend.search.advanced.nodes.critique_results",
               new=AsyncMock(return_value={"need_refine": True, "refined_query": "better"})):
        out = await nodes.node_critique(state)
    assert out["need_refine"] is True
    assert out["refined_once"] is True
    assert out["refined_query"] == "better"


@pytest.mark.asyncio
async def test_node_critique_no_refine_when_already_refined():
    from backend.search.advanced import nodes
    state = {"query": "ml", "hits": [], "refined_once": True}
    with patch("backend.search.advanced.nodes.critique_results",
               new=AsyncMock(return_value={"need_refine": True, "refined_query": "better"})):
        out = await nodes.node_critique(state)
    assert out["need_refine"] is False


def test_route_after_critique():
    from backend.search.advanced import nodes
    assert nodes._route_after_critique({"need_refine": True}) == "search"
    assert nodes._route_after_critique({"need_refine": False}) == "fit_score"


@pytest.mark.asyncio
async def test_node_fit_score_sorts_by_score():
    from backend.search.advanced import nodes
    state = {"cv_text": "cv", "preference_memory": "",
             "hits": [_hit("j1", "a"), _hit("j2", "b")]}
    scored = [
        {"job_id": "j1", "fit_score": 40, "fit_rationale": "ok", "fit_gaps": ""},
        {"job_id": "j2", "fit_score": 90, "fit_rationale": "great", "fit_gaps": ""},
    ]
    with patch("backend.search.advanced.nodes.score_fit", new=AsyncMock(return_value=scored)):
        out = await nodes.node_fit_score(state)
    assert [r["job_id"] for r in out["results"]] == ["j2", "j1"]


def test_build_graph_compiles():
    from backend.search.advanced.agent import build_graph
    from langgraph.checkpoint.memory import MemorySaver
    graph = build_graph(MemorySaver(), biencoder=MagicMock(), reranker=MagicMock(),
                        os_client=AsyncMock(), groq_client=MagicMock())
    assert graph is not None
