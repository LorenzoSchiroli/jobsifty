import os
from opensearchpy import AsyncOpenSearch

_client: AsyncOpenSearch | None = None

INDEX_NAME = "jobs_v4"
# Earlier indexes: `jobs` (single `embedding` vector), `jobs_v2` (no `posted_at`), `jobs_v3` (no job place).
# Bootstrap drops them and reconcile repopulates INDEX_NAME from Postgres.
LEGACY_INDEX_NAMES = ("jobs", "jobs_v2", "jobs_v3")
PIPELINE_NAME = "hybrid-pipeline"

_INDEX_BODY = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "job_id":                   {"type": "keyword"},
            "company_id":               {"type": "keyword"},
            "company_name":             {"type": "keyword"},
            "title":                    {"type": "text"},
            "description":              {"type": "text"},
            "chunks": {
                "type": "nested",
                "properties": {
                    "text": {"type": "text"},
                    "summary": {"type": "boolean"},
                    # the job's posted_at again: a knn filter only sees the chunk's own fields
                    "posted_at": {"type": "date"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": 384,
                        "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
                    },
                },
            },
            "employment_type":          {"type": "keyword"},
            "location_type":            {"type": "keyword"},
            "seniority":                {"type": "keyword"},
            "languages_required":       {"type": "keyword"},
            "is_consulting":            {"type": "boolean"},
            "is_startup":               {"type": "boolean"},
            "industry":                 {"type": "keyword"},
            "country":                  {"type": "keyword"},  # the company's
            # where the job is, from its location (search/places.py): city keys and country names
            "job_cities":               {"type": "keyword"},
            "job_countries":            {"type": "keyword"},
            "review_score":             {"type": "float"},
            "financial_health_score":   {"type": "integer"},
            "created_at":               {"type": "date"},
            # posted_at, else created_at: the date the age window filters on
            "posted_at":                {"type": "date"},
        }
    },
}

_PIPELINE_BODY = {
    "description": "Hybrid BM25 + kNN normalization",
    "phase_results_processors": [{
        "normalization-processor": {
            "normalization": {"technique": "min_max"},
            "combination": {
                "technique": "arithmetic_mean",
                "parameters": {"weights": [0.5, 0.5]},
            },
        }
    }],
}


def get_opensearch() -> AsyncOpenSearch:
    assert _client is not None, "OpenSearch client not initialized"
    return _client


async def get_existing_job_ids(
    os_client: AsyncOpenSearch, ids: list[str], chunk_size: int = 5000
) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        resp = await os_client.mget(index=INDEX_NAME, body={"ids": chunk}, _source=False)
        for doc in resp["docs"]:
            if doc.get("found"):
                found.add(doc["_id"])
    return found


async def drop_legacy_index(os_client: AsyncOpenSearch) -> list[str]:
    """Deletes the earlier indexes that still exist; returns their names."""
    dropped = []
    for name in LEGACY_INDEX_NAMES:
        if await os_client.indices.exists(index=name):
            await os_client.indices.delete(index=name)
            dropped.append(name)
    return dropped


async def init_opensearch() -> None:
    global _client
    url = os.environ["OPENSEARCH_URL"]
    user = os.environ.get("OPENSEARCH_USER")
    password = os.environ.get("OPENSEARCH_PASSWORD")
    kwargs: dict = {"hosts": [url]}
    if user and password:
        kwargs["http_auth"] = (user, password)
        kwargs["use_ssl"] = True
        kwargs["verify_certs"] = True
    elif user or password:
        raise ValueError("OPENSEARCH_USER and OPENSEARCH_PASSWORD must both be set")
    _client = AsyncOpenSearch(**kwargs)
    if not await _client.indices.exists(index=INDEX_NAME):
        await _client.indices.create(index=INDEX_NAME, body=_INDEX_BODY)
    await _client.indices.put_mapping(
        index=INDEX_NAME,
        body={"properties": {"created_at": {"type": "date"}}},
    )
    await _client.transport.perform_request(
        method="PUT",
        url=f"/_search/pipeline/{PIPELINE_NAME}",
        body=_PIPELINE_BODY,
    )
