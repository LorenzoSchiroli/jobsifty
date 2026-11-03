from opensearchpy import AsyncOpenSearch
from backend.opensearch_client import INDEX_NAME, PIPELINE_NAME
from backend.search.advanced.budget import HARD_FILTER_KNN_K
from backend.search.filters import SearchFilters, age_cutoff, build_clauses

_STRICT_PREFETCH = 200
# knn k when hard filters apply: the parent-field filter runs after the nested knn,
# so a larger k keeps enough candidates once it has cut some.


def build_hybrid_query(
    semantic_query: str,
    query_embedding: list[float],
    filters: SearchFilters,
    size: int = 20,
    hard: SearchFilters | None = None,
) -> dict:
    hard_clauses = build_clauses(hard.model_copy(update={"strict": True})) if hard else []
    knn: dict = {"vector": query_embedding, "k": HARD_FILTER_KNN_K if hard_clauses else 100}
    bm25: dict = {"must": {"match": {"description": semantic_query}}}
    # The age window is always hard. On the knn leg it goes inside the knn query, so the
    # k nearest chunks are found among recent jobs instead of being filtered afterwards.
    if cutoff := age_cutoff(filters):
        knn["filter"] = {"range": {"chunks.posted_at": {"gte": cutoff}}}
        bm25["filter"] = [{"range": {"posted_at": {"gte": cutoff}}}]
    legs = [
        {"bool": bm25},
        {"bool": {"must": {"nested": {
            "path": "chunks",
            "score_mode": "max",
            "query": {"knn": {"chunks.embedding": knn}},
        }}}},
    ]
    clauses = build_clauses(filters)

    if hard_clauses:
        for leg in legs:
            leg["bool"].setdefault("filter", []).extend(hard_clauses)
            leg["bool"]["should"] = clauses
        return {"query": {"hybrid": {"queries": legs}}, "size": size}

    if filters.strict:
        query: dict = {"query": {"hybrid": {"queries": legs}}, "size": _STRICT_PREFETCH}
        if clauses:
            query["post_filter"] = {"bool": {"filter": clauses}}
        return query

    for leg in legs:
        leg["bool"]["should"] = clauses
    return {"query": {"hybrid": {"queries": legs}}, "size": size}


async def hybrid_retrieve(
    client: AsyncOpenSearch,
    query_embedding: list[float],
    filters: SearchFilters,
    size: int = 20,
    hard: SearchFilters | None = None,
) -> list[dict]:
    query = build_hybrid_query(filters.semantic_query, query_embedding, filters, size=size, hard=hard)
    response = await client.search(
        index=INDEX_NAME,
        body=query,
        params={"search_pipeline": PIPELINE_NAME},
    )
    return response["hits"]["hits"]
