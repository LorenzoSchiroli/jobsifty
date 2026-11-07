import asyncio
import logging
import time
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder
from opensearchpy import AsyncOpenSearch
from pydantic import BaseModel

from backend.database import get_session
from backend.progress import Report, ignore, stream_progress, wants_progress
from backend.models import Job, User
from backend.schemas import JobSearchResponse
from backend.search.filters import ActiveFilters, SearchFilters, describe
from backend.search.models_lifecycle import get_biencoder, get_reranker, encode_query
from backend.search.query_parsing import parse_query
from backend.search.retrieval import hybrid_retrieve
from backend.search.reranker import rerank
from backend.opensearch_client import get_opensearch
from backend.auth.dependencies import get_current_user

router = APIRouter(prefix="/jobs", tags=["search"])
logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    filters: ActiveFilters  # what the query filtered on, shown above the results
    results: list[JobSearchResponse]


async def _search(query: str, session: AsyncSession, biencoder: SentenceTransformer, reranker: CrossEncoder,
                  os_client: AsyncOpenSearch, report: Report = ignore) -> SearchResponse:
    t0 = time.perf_counter()

    filters: SearchFilters = parse_query(query)
    active = describe(filters)
    t1 = time.perf_counter()

    report("Searching jobs")
    query_embedding: list[float] = await asyncio.to_thread(encode_query, biencoder, filters.semantic_query)
    t2 = time.perf_counter()

    hits = await hybrid_retrieve(os_client, query_embedding, filters)
    t3 = time.perf_counter()

    report(f"Ranking {len(hits)} match{'' if len(hits) == 1 else 'es'}")
    ranked_hits = await asyncio.to_thread(rerank, reranker, hits, filters.semantic_query, query_embedding)
    t4 = time.perf_counter()

    if not ranked_hits:
        logger.info("[search timing] parse=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs total=%.3fs (no hits)", t1-t0, t2-t1, t3-t2, t4-t3, t4-t0)
        return SearchResponse(filters=active, results=[])

    report("Loading results")
    ranked_ids = [hit["_source"]["job_id"] for hit in ranked_hits]
    result = await session.execute(
        select(Job).options(selectinload(Job.company)).where(Job.id.in_(ranked_ids))
    )
    t5 = time.perf_counter()

    logger.info("[search timing] parse=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs db=%.3fs total=%.3fs", t1-t0, t2-t1, t3-t2, t4-t3, t5-t4, t5-t0)

    jobs_by_id = {str(job.id): job for job in result.scalars()}
    return SearchResponse(filters=active, results=[
        JobSearchResponse.model_validate(jobs_by_id[id_], from_attributes=True)
        for id_ in ranked_ids if id_ in jobs_by_id])


@router.post("/search", response_model=SearchResponse)
async def search_jobs(
    body: SearchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
):
    """The active filters and the ranked jobs; with `Accept: application/x-ndjson`, progress lines
    first (backend/progress.py)."""
    def work(report: Report):
        return _search(body.query, session, biencoder, reranker, os_client, report)
    return stream_progress(work) if wants_progress(request) else await work(ignore)
