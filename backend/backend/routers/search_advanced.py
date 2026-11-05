import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langchain_core.language_models import BaseChatModel
from langgraph.types import Command
from opensearchpy import AsyncOpenSearch
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.auth.dependencies import get_current_user
from backend.database import get_session, get_session_factory
from backend.models import ApplicantProfile, Job, User
from backend.opensearch_client import get_opensearch
from backend.schemas import JobSearchResponse
from backend.search.advanced.brief import Brief
from backend.search.advanced.budget import WALL_CLOCK_SECONDS
from backend.search.advanced.context import AgentContext
from backend.search.advanced.finalize import filters_summary as summarize_filters
from backend.search.advanced.llm import get_agent_llm
from backend.search.advanced.preference_memory import get_memory, update_memory_from_session
from backend.search.models_lifecycle import get_biencoder, get_reranker

router = APIRouter(prefix="/jobs/search/advanced", tags=["search-advanced"])
logger = logging.getLogger(__name__)


class Question(BaseModel):
    text: str
    options: list[str]


class SearchLog(BaseModel):
    query: str
    filters: dict
    matches: int
    probe: str | None = None


class AdvancedJobResult(JobSearchResponse):
    fit_score: int | None
    fit_rationale: str
    fit_gaps: str
    trade_offs: list[str]


class AdvancedSearchResponse(BaseModel):
    status: Literal["done", "question", "partial"]
    thread_id: str
    question: Question | None = None
    brief: dict
    summary: str = ""
    searches: list[SearchLog] = []
    results: list[AdvancedJobResult] = []
    usage: dict | None = None


class AdvancedSearchRequest(BaseModel):
    query: str


class ResumeRequest(BaseModel):
    thread_id: str
    answer: str


def get_checkpointer():
    from backend.main import get_checkpointer as _gc
    return _gc()


def get_agent_graph():
    from backend.main import get_agent_graph as _gg
    return _gg()


def get_llm() -> BaseChatModel:
    try:
        return get_agent_llm()
    except Exception as e:
        logger.exception("[advanced] LLM not configured")
        raise HTTPException(status_code=503, detail="Advanced search is unavailable: the LLM is not configured.") from e


async def _load_cv(session: AsyncSession, user: User) -> str:
    result = await session.execute(
        select(ApplicantProfile).where(ApplicantProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile or not profile.cv_text:
        raise HTTPException(status_code=400, detail="No CV uploaded. Please upload your CV first.")
    return profile.cv_text


async def _distill_in_background(user_id: uuid.UUID, query: str, filters_summary: str,
                                 clarify_qa: list[tuple[str, str]]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        try:
            await update_memory_from_session(session, user_id, query, filters_summary, clarify_qa)
        except Exception:
            logger.exception("[advanced] memory distill failed")


async def _results(session: AsyncSession, shortlist: list[dict]) -> list[AdvancedJobResult]:
    ids = []
    for r in shortlist:
        try:
            ids.append(uuid.UUID(r["job_id"]))
        except (ValueError, KeyError):
            continue
    if not ids:
        return []
    rows = await session.execute(select(Job).options(selectinload(Job.company)).where(Job.id.in_(ids)))
    jobs = {str(j.id): j for j in rows.scalars()}
    out = []
    for r in shortlist:
        job = jobs.get(str(r["job_id"]))
        if job is None:
            continue
        base = JobSearchResponse.model_validate(job, from_attributes=True)
        out.append(AdvancedJobResult(
            **base.model_dump(), fit_score=r.get("fit_score"), fit_rationale=r.get("fits", ""),
            fit_gaps="; ".join(r.get("gaps") or []), trade_offs=list(r.get("trade_offs") or []),
        ))
    return out


def _pending_interrupts(snap) -> list:
    return [i for task in snap.tasks for i in task.interrupts]


async def _delete_thread(checkpointer, thread_id: str) -> None:
    try:
        await checkpointer.adelete_thread(thread_id)
    except Exception:  # never hides the run's own answer or error
        logger.exception("[advanced] could not delete thread %s", thread_id)


async def run_agent(graph, checkpointer, ctx: AgentContext, payload, thread_id: str, session: AsyncSession,
                    background: BackgroundTasks) -> AdvancedSearchResponse:
    """Runs the graph until a question or the end. The thread is deleted unless a question is open,
    also when the run raises (durability="exit" saves on an error too); the error still propagates."""
    config = {"configurable": {"thread_id": thread_id}}
    question_open = False
    try:
        await graph.ainvoke(payload, config, context=ctx, durability="exit")
        snap = await graph.aget_state(config)
        values = snap.values or {}
        brief = Brief.model_validate(values["brief"]).to_api() if values.get("brief") else {}

        if interrupts := _pending_interrupts(snap):
            value = interrupts[0].value
            question_open = True
            return AdvancedSearchResponse(
                status="question", thread_id=thread_id, brief=brief,
                question=Question(text=value["question"], options=list(value.get("options") or [])),
            )

        status = values.get("status", "partial")
        results = await _results(session, values.get("shortlist") or [])
        # matches: jobs meeting every soft filter of the search, or the jobs returned when it set none
        searches = [SearchLog(query=s["query"], filters=s.get("filters") or {}, matches=s.get("matches", 0),
                              probe=s.get("probe")) for s in values.get("searches") or []]
        response = AdvancedSearchResponse(
            status=status, thread_id=thread_id, brief=brief, summary=values.get("summary", ""),
            searches=searches, results=results, usage=values.get("usage"),
        )
        if status == "done":
            qa = [(q["question"], q["answer"]) for q in values.get("qa") or []]
            background.add_task(_distill_in_background, uuid.UUID(values["user_id"]), values.get("query", ""),
                                summarize_filters(values), qa)
        return response
    finally:
        if not question_open:
            await _delete_thread(checkpointer, thread_id)


async def _context(session: AsyncSession, user: User, llm, biencoder, reranker, os_client) -> AgentContext:
    cv_text = await _load_cv(session, user)
    memory = await get_memory(session, user.id)
    return AgentContext(
        user_id=user.id, run_id=str(uuid.uuid4()), cv_text=cv_text,
        preference_memory=memory.memory_text if memory else "", llm=llm, biencoder=biencoder,
        reranker=reranker, os_client=os_client, session_factory=get_session_factory(),
        deadline=time.monotonic() + WALL_CLOCK_SECONDS, now=datetime.now(timezone.utc),
    )


@router.post("", response_model=AdvancedSearchResponse)
async def start_advanced_search(
    body: AdvancedSearchRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
    llm: BaseChatModel = Depends(get_llm),
    checkpointer=Depends(get_checkpointer),
    graph=Depends(get_agent_graph),
) -> AdvancedSearchResponse:
    ctx = await _context(session, current_user, llm, biencoder, reranker, os_client)
    thread_id = str(uuid.uuid4())
    payload = {"messages": [("human", body.query)], "user_id": str(current_user.id), "query": body.query,
               "brief": Brief().model_dump()}
    return await run_agent(graph, checkpointer, ctx, payload, thread_id, session, background)


@router.post("/resume", response_model=AdvancedSearchResponse)
async def resume_advanced_search(
    body: ResumeRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    biencoder: SentenceTransformer = Depends(get_biencoder),
    reranker: CrossEncoder = Depends(get_reranker),
    os_client: AsyncOpenSearch = Depends(get_opensearch),
    llm: BaseChatModel = Depends(get_llm),
    checkpointer=Depends(get_checkpointer),
    graph=Depends(get_agent_graph),
) -> AdvancedSearchResponse:
    snap = await graph.aget_state({"configurable": {"thread_id": body.thread_id}})
    values = snap.values or {}
    if not values or values.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=404, detail="Search not found.")
    if not _pending_interrupts(snap):  # an orphan: a thread left behind with no question open
        await _delete_thread(checkpointer, body.thread_id)
        raise HTTPException(status_code=404, detail="Search not found.")
    ctx = await _context(session, current_user, llm, biencoder, reranker, os_client)
    return await run_agent(graph, checkpointer, ctx, Command(resume=body.answer), body.thread_id, session, background)
