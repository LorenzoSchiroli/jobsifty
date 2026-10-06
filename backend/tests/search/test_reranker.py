from unittest.mock import MagicMock
from backend.search.reranker import rerank

Q = [1.0, 0.0]


def _hit(job_id: str, title: str = "") -> dict:
    return {"_source": {"job_id": job_id, "title": title}}


def test_sorts_by_score_descending():
    reranker = MagicMock()
    reranker.predict.return_value = [0.2, 0.9, 0.5]
    result = rerank(reranker, [_hit("a"), _hit("b"), _hit("c")], "python", Q)
    assert [h["_source"]["job_id"] for h in result] == ["b", "c", "a"]


def test_respects_top_k():
    reranker = MagicMock()
    reranker.predict.return_value = list(range(30))
    result = rerank(reranker, [_hit(str(i)) for i in range(30)], "x", Q, top_k=20)
    assert len(result) == 20


def test_builds_correct_pairs():
    reranker = MagicMock()
    reranker.predict.return_value = [0.5]
    rerank(reranker, [_hit("a", "ml engineer")], "python ml", Q)
    assert reranker.predict.call_args[0][0] == [("python ml", "ml engineer")]


def test_picks_chunk_closest_to_query_embedding():
    reranker = MagicMock()
    reranker.predict.return_value = [0.5]
    hit = {"_source": {"job_id": "a", "chunks": [
        {"text": "company benefits", "embedding": [0.0, 1.0]},
        {"text": "pytorch distributed training", "embedding": [1.0, 0.0]},
    ]}}
    rerank(reranker, [hit], "pytorch", [0.9, 0.1])
    assert reranker.predict.call_args[0][0] == [("pytorch", "pytorch distributed training")]


def test_job_without_chunks_is_scored_on_its_title():
    reranker = MagicMock()
    reranker.predict.return_value = [0.5]
    rerank(reranker, [{"_source": {"job_id": "a", "title": "Data Scientist", "chunks": []}}], "x", Q)
    assert reranker.predict.call_args[0][0] == [("x", "Data Scientist")]
