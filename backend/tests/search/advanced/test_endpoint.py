import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.auth.dependencies import get_current_user
from backend.database import get_session
from backend.main import app
from backend.models import ApplicantProfile, Company, Job, User
from backend.opensearch_client import get_opensearch
from backend.routers import search_advanced as router
from backend.search.advanced.agent import build_graph
from backend.search.advanced.finalize import FinalShortlist, ShortlistItem
from backend.search.models_lifecycle import get_biencoder, get_reranker
from tests.search.advanced.conftest import JOB_A
from tests.search.advanced.fakes import ScriptedChatModel, tool_call
from tests.search.advanced.samples import REMOTE

pytestmark = pytest.mark.asyncio

SHORT = JOB_A[:4]
SEARCH = tool_call("search_jobs", {"variants": [{"query": "ml engineer"}]})
FINISH = tool_call("finish", {"keep": [], "notes": ""})
BRIEF = tool_call("update_brief", {"items": [REMOTE.model_dump()]})
ASK = tool_call("ask_user", {"question": "Allow hybrid?",
                             "options": [{"label": "Allow hybrid"}, {"label": "Keep remote only"}]})

SEARCH_THEN_FINISH = [BRIEF, SEARCH, FINISH]
ASK_THEN_FINISH = [BRIEF, SEARCH, ASK, FINISH]  # a question only after a search
ASK_THEN_SEARCH_THEN_FINISH = [BRIEF, SEARCH, ASK, tool_call("search_jobs", {"variants": [{"query": "ml"}]}), FINISH]


def shortlist_of(score: int) -> FinalShortlist:
    return FinalShortlist(summary="Good match.", shortlist=[ShortlistItem(
        job_id=SHORT, fit_score=score, fits="remote ML", trade_offs=["not a startup"], gaps=["no k8s", "no Go"])])


@pytest_asyncio.fixture
async def env(engine, patched_run_variant):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as s:
        s.add(User(id=user_id, username="advuser", password_hash="x"))
        await s.flush()
        s.add(ApplicantProfile(user_id=user_id, cv_text="5yr ML dev"))
        company = Company(name="acme")
        s.add(company)
        await s.flush()
        s.add(Job(id=uuid.UUID(JOB_A), url="https://ex.com/1", title="ML Engineer", company_id=company.id))
        await s.commit()
    return factory, user_id


def _install(factory, user_id, saver, llm):
    async def override_session():
        async with factory() as s:
            yield s

    user = User(id=user_id, username="advuser", password_hash="x")
    graph = build_graph(saver)
    app.dependency_overrides.update({
        get_session: override_session, get_biencoder: lambda: MagicMock(), get_reranker: lambda: MagicMock(),
        get_opensearch: lambda: AsyncMock(), get_current_user: lambda: user,
        router.get_checkpointer: lambda: saver, router.get_agent_graph: lambda: graph,
        router.get_llm: lambda: llm,
    })


@pytest.fixture
def saver():
    return MemorySaver()


@pytest.fixture
def distill_mock():
    mock = AsyncMock()
    with patch("backend.routers.search_advanced.update_memory_from_session", mock), \
         patch("backend.routers.search_advanced.get_session_factory", return_value=MagicMock()):
        yield mock


@pytest_asyncio.fixture
async def adv_client(env, saver, distill_mock):
    """Call with (script, structured) to install a scripted LLM; returns (client, llm)."""
    factory, user_id = env
    holder = {}

    def make(script, structured):
        llm = ScriptedChatModel(script=list(script), structured=list(structured))
        _install(factory, user_id, saver, llm)
        return holder["ac"], llm

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        holder["ac"] = ac
        yield make
    app.dependency_overrides.clear()


@pytest.fixture
def other_user_override():
    def go():
        other = User(id=uuid.uuid4(), username="other", password_hash="x")
        app.dependency_overrides[get_current_user] = lambda: other
    return go


async def test_run_without_question_returns_done(adv_client):
    ac, _ = adv_client(SEARCH_THEN_FINISH, [shortlist_of(88)])
    r = await ac.post("/jobs/search/advanced", json={"query": "remote ml"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "done"
    result = body["results"][0]
    assert result["id"] == JOB_A and result["fit_score"] == 88 and result["company"]["name"] == "acme"
    assert result["trade_offs"] == ["not a startup"] and result["fit_gaps"] == "no k8s; no Go"
    assert result["fit_rationale"] == "remote ML" and body["searches"][0]["query"] == "ml engineer"
    assert body["summary"] == "Good match."


async def test_done_run_leaves_no_checkpoint(adv_client, saver):
    ac, _ = adv_client(SEARCH_THEN_FINISH, [shortlist_of(88)])
    body = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    assert await saver.aget_tuple({"configurable": {"thread_id": body["thread_id"]}}) is None


async def test_question_then_resume(adv_client, saver):
    ac, _ = adv_client(ASK_THEN_FINISH, [shortlist_of(70)])
    q = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    assert q["status"] == "question" and q["question"]["text"] == "Allow hybrid?"
    assert q["question"]["options"] == ["Allow hybrid", "Keep remote only"] and q["results"] == []
    assert await saver.aget_tuple({"configurable": {"thread_id": q["thread_id"]}}) is not None
    r = (await ac.post("/jobs/search/advanced/resume",
                       json={"thread_id": q["thread_id"], "answer": "Keep remote only"})).json()
    assert r["status"] == "done"


async def test_resume_other_users_thread_is_404(adv_client, other_user_override):
    ac, _ = adv_client(ASK_THEN_FINISH, [])
    q = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    other_user_override()
    r = await ac.post("/jobs/search/advanced/resume", json={"thread_id": q["thread_id"], "answer": "A"})
    assert r.status_code == 404


async def test_resume_finished_thread_is_404(adv_client):
    ac, _ = adv_client(SEARCH_THEN_FINISH, [shortlist_of(88)])
    done = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    r = await ac.post("/jobs/search/advanced/resume", json={"thread_id": done["thread_id"], "answer": "A"})
    assert r.status_code == 404


async def test_llm_failure_returns_partial_not_500(adv_client, distill_mock):
    ac, _ = adv_client([BRIEF, SEARCH, RuntimeError("429")], [])
    r = await ac.post("/jobs/search/advanced", json={"query": "x"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "partial" and body["results"][0]["fit_score"] is None
    distill_mock.assert_not_awaited()


async def test_shortlisted_job_gone_from_postgres_is_skipped(adv_client, env):
    factory, _ = env
    ac, _ = adv_client(SEARCH_THEN_FINISH, [shortlist_of(88)])
    async with factory() as s:
        await s.delete(await s.get(Job, uuid.UUID(JOB_A)))
        await s.commit()
    body = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    assert body["status"] == "done" and body["results"] == []


async def test_advanced_requires_cv(adv_client, env):
    factory, user_id = env
    ac, _ = adv_client([], [])
    async with factory() as s:
        await s.execute(delete(ApplicantProfile).where(ApplicantProfile.user_id == user_id))
        await s.commit()
    r = await ac.post("/jobs/search/advanced", json={"query": "x"})
    assert r.status_code == 400 and "CV" in r.json()["detail"]


async def test_missing_llm_config_is_503(adv_client, monkeypatch):
    ac, _ = adv_client([], [])
    del app.dependency_overrides[router.get_llm]
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ADVANCED_LLM_API_KEY", raising=False)
    r = await ac.post("/jobs/search/advanced", json={"query": "x"})
    assert r.status_code == 503


async def test_distill_gets_filters_and_answers(adv_client, distill_mock):
    ac, _ = adv_client(ASK_THEN_SEARCH_THEN_FINISH, [shortlist_of(80)])
    q = (await ac.post("/jobs/search/advanced", json={"query": "x"})).json()
    await ac.post("/jobs/search/advanced/resume", json={"thread_id": q["thread_id"], "answer": "Keep remote only"})
    args = distill_mock.await_args.args
    assert "location_type=remote" in args[3] and args[4] == [("Allow hybrid?", "Keep remote only")]
