import numpy as np
from sentence_transformers.cross_encoder import CrossEncoder


def _best_chunk(chunks: list[dict], query_embedding: list[float]) -> str | None:
    # Same pick as the k-NN leg's score_mode "max": the chunk closest to the query.
    # Hybrid queries drop inner_hits, so the vectors come back in _source instead.
    usable = [c for c in chunks if c.get("embedding") and c.get("text")]
    if not usable:
        return None
    scores = np.asarray([c["embedding"] for c in usable]) @ np.asarray(query_embedding)
    return usable[int(scores.argmax())]["text"]


def _hit_text(hit: dict, query_embedding: list[float]) -> str:
    source = hit.get("_source", {})
    return _best_chunk(source.get("chunks") or [], query_embedding) or source.get("title") or ""


def rerank(
    reranker: CrossEncoder,
    hits: list[dict],
    semantic_query: str,
    query_embedding: list[float],
    top_k: int = 20,
) -> list[dict]:
    pairs = [(semantic_query, _hit_text(hit, query_embedding)) for hit in hits]
    scores = reranker.predict(pairs)
    ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)
    return [hit for hit, _ in ranked[:top_k]]
