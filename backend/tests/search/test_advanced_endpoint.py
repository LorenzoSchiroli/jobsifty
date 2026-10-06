import uuid
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker
from langgraph.checkpoint.memory import MemorySaver

from backend.main import app
from backend.routers.search_advanced import get_checkpointer
from backend.database import get_session
from backend.search.models_lifecycle import get_biencoder, get_reranker
from backend.search.query_understanding import get_groq_client
from backend.opensearch_client import get_opensearch
from backend.auth.dependencies import get_current_user
from backend.models import ApplicantProfile, Company, Job, User

pytestmark = pytest.mark.asyncio

# NOTE: override the router's get_checkpointer (the dependency the endpoints actually
# use). backend.main.get_checkpointer is a different object and would not take effect.


def _mock_groq(semantic_query="ml engineer"):
    msg = MagicMock(); msg.content = json.dumps({"semantic_query": semantic_query})
    choice = MagicMock(); choice.message = msg
    completion = MagicMock(); completion.choices = [choice]
    client = MagicMock(); client.chat.completions.create.return_value = completion
    return client


@pytest_asyncio.fixture
async def adv_client(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with factory() as session:
        session.add(User(id=user_id, username="advuser", password_hash="x"))
        await session.flush()
        session.add(ApplicantProfile(user_id=user_id, cv_text="5yr ML dev"))
        company = Company(name="acme"); session.add(company); await session.flush()
        session.add(Job(id=job_id, url="https://ex.com/1", title="ML Engineer", company_id=company.id))
        await session.commit()
    mock_user = User(id=user_id, username="advuser", password_hash="x")

    async def override_session():
        async with factory() as session:
            yield session

    biencoder = MagicMock()
    enc = MagicMock(); enc.tolist.return_value = [0.0] * 384
    biencoder.encode.return_value = enc
    os_mock = AsyncMock()
    os_mock.search.return_value = {"hits": {"hits": [
        {"_source": {"job_id": str(job_id), "title": "ml engineer"}}
    ]}}

    saver = MemorySaver()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_biencoder] = lambda: biencoder
    app.dependency_overrides[get_reranker] = lambda: MagicMock()
    app.dependency_overrides[get_groq_client] = lambda: _mock_groq()
    app.dependency_overrides[get_opensearch] = lambda: os_mock
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_checkpointer] = lambda: saver

    with patch("backend.main.init_models"), \
         patch("backend.main.init_opensearch", new_callable=AsyncMock), \
         patch("backend.search.advanced.nodes.rerank",
               side_effect=lambda r, hits, q, top_k=20: hits), \
         patch("backend.search.advanced.nodes.generate_clarify_questions",
               new=AsyncMock(return_value=["Remote only?"])), \
         patch("backend.search.advanced.nodes.critique_results",
               new=AsyncMock(return_value={"need_refine": False, "refined_query": None})), \
         patch("backend.search.advanced.nodes.score_fit",
               new=AsyncMock(return_value=[{"job_id": str(job_id), "fit_score": 88,
                                            "fit_rationale": "strong", "fit_gaps": "no k8s"}])), \
         patch("backend.search.advanced.preference_memory.distill_memory",
               new=AsyncMock(return_value="prefers remote ml")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, str(job_id)
    app.dependency_overrides.clear()


async def test_advanced_then_resume_returns_fit_scored(adv_client):
    ac, job_id = adv_client
    start = await ac.post("/jobs/search/advanced", json={"query": "ml engineer"})
    assert start.status_code == 200
    body = start.json()
    assert body["clarify_questions"] == ["Remote only?"]
    thread_id = body["thread_id"]

    resume = await ac.post("/jobs/search/advanced/resume",
                           json={"thread_id": thread_id, "clarify_answers": ["yes"]})
    assert resume.status_code == 200
    results = resume.json()
    assert len(results) == 1
    assert results[0]["id"] == job_id
    assert results[0]["fit_score"] == 88
    assert results[0]["fit_gaps"] == "no k8s"
    assert results[0]["company"]["name"] == "acme"


async def test_advanced_requires_cv(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as session:
        session.add(User(id=user_id, username="nocv", password_hash="x"))
        await session.commit()
    mock_user = User(id=user_id, username="nocv", password_hash="x")

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_biencoder] = lambda: MagicMock()
    app.dependency_overrides[get_reranker] = lambda: MagicMock()
    app.dependency_overrides[get_groq_client] = lambda: _mock_groq()
    app.dependency_overrides[get_opensearch] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_checkpointer] = lambda: MemorySaver()

    with patch("backend.main.init_models"), \
         patch("backend.main.init_opensearch", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/jobs/search/advanced", json={"query": "x"})
    app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert "CV" in resp.json()["detail"]
