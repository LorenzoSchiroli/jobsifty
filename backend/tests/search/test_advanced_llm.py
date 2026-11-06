import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from backend.models import User


def _mock_llm(content: str):
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def test_agent_llm_defaults_to_groq(monkeypatch):
    from backend.search.advanced import llm
    for name in ("ADVANCED_LLM_BASE_URL", "ADVANCED_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_MODEL_LARGE", "groq-large")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    model = llm.get_agent_llm()
    assert (model.model_name, model.openai_api_key.get_secret_value()) == ("groq-large", "gsk-test")
    assert model.openai_api_base == "https://api.groq.com/openai/v1"
    assert (model.max_retries, model.request_timeout, model.reasoning_effort) == (3, 60, "low")


def test_agent_llm_env_overrides_are_read_at_call_time(monkeypatch):
    from backend.search.advanced import llm
    monkeypatch.setenv("ADVANCED_LLM_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("ADVANCED_LLM_MODEL", "other-model")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    model = llm.get_agent_llm()
    assert (model.model_name, model.openai_api_key.get_secret_value(), model.openai_api_base) == (
        "other-model", "gsk-test", "http://localhost:9000/v1")


async def test_distill_memory_returns_text():
    from backend.search.advanced import llm
    with patch.object(llm, "get_agent_llm", return_value=_mock_llm("prefers remote ml roles in EU")):
        out = await llm.distill_memory("", False, "ml engineer", "remote=true", [("Remote?", "yes")])
    assert "remote" in out.lower()


async def test_distill_memory_empty_answer_keeps_existing_memory():
    from backend.search.advanced import llm
    with patch.object(llm, "get_agent_llm", return_value=_mock_llm("   ")):
        out = await llm.distill_memory("likes remote", True, "q", "", [])
    assert out == "likes remote"


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
