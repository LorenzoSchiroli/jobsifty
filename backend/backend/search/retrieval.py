from opensearchpy import AsyncOpenSearch
from backend.opensearch_client import INDEX_NAME, PIPELINE_NAME
from backend.search.filters import SearchFilters, build_clauses

_STRICT_PREFETCH = 200


def build_hybrid_query(
    semantic_query: str,
    query_embedding: list[float],
    filters: SearchFilters,
    size: int = 20,
) -> dict:
    legs = [
        {"bool": {"must": {"match": {"description": semantic_query}}}},
        {"bool": {"must": {"nested": {
            "path": "chunks",
            "score_mode": "max",
            "query": {"knn": {"chunks.embedding": {"vector": query_embedding, "k": 100}}},
        }}}},
    ]
    clauses = build_clauses(filters)

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
) -> list[dict]:
    query = build_hybrid_query(filters.semantic_query, query_embedding, filters)
    response = await client.search(
        index=INDEX_NAME,
        body=query,
        params={"search_pipeline": PIPELINE_NAME},
    )
    return response["hits"]["hits"]
