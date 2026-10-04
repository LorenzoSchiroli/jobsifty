import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder
from opensearchpy import AsyncOpenSearch
from groq import Groq
from langgraph.types import Command

from backend.database import get_session, get_session_factory
from backend.models import ApplicantProfile, Job, User
from backend.schemas import JobSearchResponse
from backend.search.models_lifecycle import get_biencoder, get_reranker
from backend.search.query_understanding import get_groq_client
from backend.opensearch_client import get_opensearch
from backend.auth.dependencies import get_current_user
from backend.search.advanced.agent import build_graph
from backend.search.advanced.preference_memory import get_memory, update_memory_from_session

router = APIRouter(prefix="/jobs/search/advanced", tags=["search-advanced"])
logger = logging.getLogger(__name__)


class AdvancedSearchRequest(BaseModel):
    query: str


class AdvancedSearchStart(BaseModel):
    thread_id: str
    clarify_questions: list[str]


class ResumeRequest(BaseModel):
    thread_id: str
    clarify_answers: list[str]


class AdvancedJobResult(JobSearchResponse):
    fit_score: int
    fit_rationale: str
    fit_gaps: str


async def _load_cv(session: AsyncSession, user: User) -> str:
    result = await session.execute(
        select(ApplicantProfile).where(ApplicantProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile or not profile.cv_text:
        raise HTTPException(status_code=400, detail="No CV uploaded. Please upload your CV first.")
    return profile.cv_text


async def _distill_in_background(user_id: uuid.UUID, query: str, clarify_qa: list[tuple[str, str]]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        try:
            await update_memory_from_session(session, user_id, query, "", clarify_qa)
        except Exception:
            logger.exception("[advanced] memory distill failed")


def get_checkpointer():
    from backend.main import get_checkpointer as _gc
    return _gc()


@router.post("", response_model=AdvancedSearchStart)
async def start_advanced_search(
    body: AdvancedSearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    groq_client: Groq = Depends(get_groq_client),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
    checkpointer=Depends(get_checkpointer),
) -> AdvancedSearchStart:
    cv_text = await _load_cv(session, current_user)
    memory = await get_memory(session, current_user.id)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph = build_graph(checkpointer, biencoder=biencoder, reranker=reranker,
                        os_client=os_client, groq_client=groq_client)
    init_state = {
        "query": body.query,
        "cv_text": cv_text,
        "preference_memory": memory.memory_text if memory else "",
        "refined_once": False,
    }
    await graph.ainvoke(init_state, config)
    snap = await graph.aget_state(config)
    interrupts = [i for task in snap.tasks for i in task.interrupts]
    questions = interrupts[0].value.get("clarify_questions", []) if interrupts else []
    return AdvancedSearchStart(thread_id=thread_id, clarify_questions=questions)


@router.post("/resume", response_model=list[AdvancedJobResult])
async def resume_advanced_search(
    body: ResumeRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    groq_client: Groq = Depends(get_groq_client),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
    checkpointer=Depends(get_checkpointer),
) -> list[AdvancedJobResult]:
    config = {"configurable": {"thread_id": body.thread_id}}
    graph = build_graph(checkpointer, biencoder=biencoder, reranker=reranker,
                        os_client=os_client, groq_client=groq_client)
    await graph.ainvoke(Command(resume=body.clarify_answers), config)
    snap = await graph.aget_state(config)
    values = snap.values or {}
    scored = values.get("results", [])
    if not scored:
        return []

    job_ids = [r["job_id"] for r in scored if r.get("job_id")]
    if not job_ids:
        return []
    result = await session.execute(
        select(Job).options(selectinload(Job.company)).where(Job.id.in_(job_ids))
    )
    jobs_by_id = {str(job.id): job for job in result.scalars()}

    response: list[AdvancedJobResult] = []
    for r in scored:  # already sorted by fit_score desc
        jid = r.get("job_id")
        if not jid:
            continue
        job = jobs_by_id.get(jid)
        if job is None:
            continue
        base = JobSearchResponse.model_validate(job, from_attributes=True)
        response.append(AdvancedJobResult(
            **base.model_dump(),
            fit_score=int(r.get("fit_score", 0)),
            fit_rationale=r.get("fit_rationale", ""),
            fit_gaps=r.get("fit_gaps", ""),
        ))

    questions = values.get("clarify_questions", []) or []
    answers = values.get("clarify_answers", []) or []
    clarify_qa = list(zip(questions, answers))
    background.add_task(_distill_in_background, current_user.id, values.get("query", ""), clarify_qa)

    return response
