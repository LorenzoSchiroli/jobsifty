import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.models import User

pytestmark = pytest.mark.asyncio


def _mock_llm(content: str):
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


async def test_generate_clarify_questions_parses_list():
    from backend.search.advanced import llm
    with patch.object(llm, "large_llm", return_value=_mock_llm('["Remote only?", "Which countries?"]')):
        qs = await llm.generate_clarify_questions("ml engineer", "cv", "")
    assert qs == ["Remote only?", "Which countries?"]


async def test_critique_results_parses_dict():
    from backend.search.advanced import llm
    payload = json.dumps({"need_refine": True, "refined_query": "senior ml engineer pytorch"})
    with patch.object(llm, "large_llm", return_value=_mock_llm(payload)):
        out = await llm.critique_results("ml", [{"_source": {"title": "x"}}])
    assert out == {"need_refine": True, "refined_query": "senior ml engineer pytorch"}


async def test_score_fit_parses_scored_list():
    from backend.search.advanced import llm
    payload = json.dumps([
        {"job_id": "j1", "fit_score": 82, "fit_rationale": "strong overlap", "fit_gaps": "no kubernetes"}
    ])
    hits = [{"_source": {"job_id": "j1", "title": "ml role"}}]
    with patch.object(llm, "large_llm", return_value=_mock_llm(payload)):
        out = await llm.score_fit("cv", "", hits)
    assert out[0]["job_id"] == "j1"
    assert out[0]["fit_score"] == 82
    assert out[0]["fit_gaps"] == "no kubernetes"


async def test_score_fit_falls_back_to_unranked_hits_on_parse_failure():
    from backend.search.advanced import llm
    hits = [
        {"_source": {"job_id": "j1", "title": "ml role"}},
        {"_source": {"job_id": "j2", "title": "data role"}},
    ]
    with patch.object(llm, "large_llm", return_value=_mock_llm("not valid json")):
        out = await llm.score_fit("cv", "", hits)
    assert [r["job_id"] for r in out] == ["j1", "j2"]
    assert all(r["fit_score"] == 0 for r in out)
    assert all(r["fit_rationale"] == "" for r in out)
    assert all(r["fit_gaps"] == "" for r in out)


def test_job_text_uses_title_company_and_cleaned_text():
    from backend.search.advanced import llm
    hit = {"_source": {"title": "ML Engineer", "company_name": "acme", "chunks": [
        {"text": "ML Engineer\nBuild ranking models in PyTorch.", "embedding": [0.1]},
        {"text": "ML Engineer\nlater part", "embedding": [0.2]},
    ]}}
    assert llm.job_text(hit, 1500) == "ML Engineer at acme\nBuild ranking models in PyTorch."
    assert llm.job_text(hit, 11) == "ML Engineer"
    assert llm.job_text({"_source": {"title": "Data Scientist"}}, 1500) == "Data Scientist"


async def test_score_fit_sends_only_the_top_jobs():
    from backend.search.advanced import llm
    hits = [{"_source": {"job_id": f"j{i}", "title": f"role {i}"}} for i in range(25)]
    mock = _mock_llm("[]")
    with patch.object(llm, "large_llm", return_value=mock):
        await llm.score_fit("cv", "", hits)
    prompt = mock.ainvoke.call_args.args[0][1].content
    assert '"j9"' in prompt and '"j10"' not in prompt


async def test_distill_memory_returns_text():
    from backend.search.advanced import llm
    with patch.object(llm, "large_llm", return_value=_mock_llm("prefers remote ml roles in EU")):
        out = await llm.distill_memory("", False, "ml engineer", "remote=true", [("Remote?", "yes")])
    assert "remote" in out.lower()


async def test_update_memory_from_session_persists_distilled(db_session):
    from backend.search.advanced import preference_memory as svc
    user = User(id=uuid.uuid4(), username=f"u{uuid.uuid4().hex[:6]}", password_hash="x")
    db_session.add(user)
    await db_session.flush()

    with patch("backend.search.advanced.preference_memory.distill_memory",
               new=AsyncMock(return_value="distilled blob")):
        pm = await svc.update_memory_from_session(db_session, user.id, "ml engineer", "remote=true", [("Remote?", "yes")])
    assert pm.memory_text == "distilled blob"
    assert pm.user_edited is False


async def test_update_memory_preserves_user_edited_flag(db_session):
    from backend.search.advanced import preference_memory as svc
    user = User(id=uuid.uuid4(), username=f"u{uuid.uuid4().hex[:6]}", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    await svc.set_memory(db_session, user.id, "user wrote this")  # user_edited=True

    captured = {}
    async def fake_distill(existing, user_edited, *a, **k):
        captured["existing"] = existing
        captured["user_edited"] = user_edited
        return existing + " + appended"
    with patch("backend.search.advanced.preference_memory.distill_memory", new=fake_distill):
        pm = await svc.update_memory_from_session(db_session, user.id, "q", "f", [])
    assert captured["user_edited"] is True
    assert captured["existing"] == "user wrote this"
    assert pm.user_edited is True  # stays True after distill
