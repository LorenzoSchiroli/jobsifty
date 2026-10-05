import logging
import time
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder
from opensearchpy import AsyncOpenSearch
from pydantic import BaseModel

from backend.database import get_session
from backend.models import Job, User
from backend.schemas import JobSearchResponse
from backend.search.filters import SearchFilters
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


@router.post("/search", response_model=list[JobSearchResponse])
async def search_jobs(
    body: SearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
) -> list[JobSearchResponse]:
    t0 = time.perf_counter()

    filters: SearchFilters = parse_query(body.query)
    t1 = time.perf_counter()

    query_embedding: list[float] = encode_query(biencoder, filters.semantic_query)
    t2 = time.perf_counter()

    hits = await hybrid_retrieve(os_client, query_embedding, filters)
    t3 = time.perf_counter()

    ranked_hits = rerank(reranker, hits, filters.semantic_query, query_embedding)
    t4 = time.perf_counter()

    if not ranked_hits:
        logger.info("[search timing] parse=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs total=%.3fs (no hits)", t1-t0, t2-t1, t3-t2, t4-t3, t4-t0)
        return []

    ranked_ids = [hit["_source"]["job_id"] for hit in ranked_hits]
    result = await session.execute(
        select(Job).options(selectinload(Job.company)).where(Job.id.in_(ranked_ids))
    )
    t5 = time.perf_counter()

    logger.info("[search timing] parse=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs db=%.3fs total=%.3fs", t1-t0, t2-t1, t3-t2, t4-t3, t5-t4, t5-t0)

    jobs_by_id = {str(job.id): job for job in result.scalars()}
    return [jobs_by_id[id_] for id_ in ranked_ids if id_ in jobs_by_id]
