from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion import pool as pool_module
from ingestion.llm import Gemini, Groq, LLMPool, Nvidia

_POST = "ingestion.llm.requests.post"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("NVIDIA_NIM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "LLM_PROVIDERS",
                "NVIDIA_MODEL", "GEMINI_MODEL", "GROQ_MODEL_BASE"):
        monkeypatch.delenv(key, raising=False)
    for cls in (Nvidia, Gemini, Groq):
        monkeypatch.setattr(cls, "min_interval", 0.0)


def _reply(content: str = "{}", status: int = 200, text: str = "", headers: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status, text=text, headers=headers or {})
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _all_keys(monkeypatch):
    for key in ("NVIDIA_NIM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(key, "k")


def test_default_order_puts_the_private_provider_last(monkeypatch):
    _all_keys(monkeypatch)
    llm = LLMPool.from_env()
    assert [p.name for p in llm.providers] == ["nvidia", "gemini", "groq"]
    assert [p.name for p in LLMPool.from_env(private_only=True).providers] == ["groq"]


def test_order_and_models_from_env(monkeypatch):
    _all_keys(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "groq,nvidia")
    monkeypatch.setenv("GROQ_MODEL_BASE", "openai/gpt-oss-120b")
    llm = LLMPool.from_env()
    assert [(p.name, p.model) for p in llm.providers] == [
        ("groq", "openai/gpt-oss-120b"), ("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
    ]


def test_request_is_openai_compatible_json_mode(monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    with patch(_POST, return_value=_reply('{"a": 1}')) as post:
        assert LLMPool.from_env().complete("hello", lambda c: c) == '{"a": 1}'
    assert post.call_args.args[0] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer nv-key"}
    body = post.call_args.kwargs["json"]
    assert body["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["response_format"] == {"type": "json_object"}


def test_daily_quota_disables_a_provider_and_falls_through(monkeypatch):
    _all_keys(monkeypatch)
    llm = LLMPool.from_env()
    daily = _reply(status=429, text='{"error": "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel"}')
    with patch(_POST, side_effect=[_reply(status=500), daily, _reply("ok")]):
        assert llm.complete("p", lambda c: c) == "ok"
    nvidia, gemini, groq = llm.providers
    assert not nvidia.disabled and gemini.disabled and groq.ok == 1


def test_per_minute_limit_only_pauses(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    llm = LLMPool.from_env()
    tpm = _reply(status=429, text="Rate limit reached on tokens per minute (TPM)", headers={"Retry-After": "7"})
    with patch(_POST, return_value=tpm) as post:
        assert llm.complete("p", lambda c: c) is None
        assert llm.complete("p", lambda c: c) is None
    groq = llm.providers[-1]
    assert post.call_count == 1 and not groq.disabled and not groq.usable()
    assert not llm.down()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_bad_key_or_unknown_model_disables_for_the_run(monkeypatch, status):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "k")
    llm = LLMPool.from_env()
    with patch(_POST, return_value=_reply(status=status, text="nope")) as post:
        assert llm.complete("p", lambda c: c) is None
        assert llm.complete("p", lambda c: c) is None
    assert post.call_count == 1 and llm.down()


def test_timeout_moves_to_the_next_provider(monkeypatch):
    _all_keys(monkeypatch)
    llm = LLMPool.from_env()
    with patch(_POST, side_effect=[requests.Timeout("slow"), _reply("ok")]):
        assert llm.complete("p", lambda c: c) == "ok"
    assert llm.providers[0].failed == 1 and llm.providers[1].ok == 1


def test_calls_are_spaced_per_provider(monkeypatch):
    monkeypatch.setattr(Groq, "min_interval", 12.0)
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(pool_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(pool_module.time, "sleep", sleep)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    llm = LLMPool.from_env()
    with patch(_POST, return_value=_reply()):
        llm.complete("p", lambda c: c)
        now[0] += 3.0  # the next company comes 3 s later
        llm.complete("p", lambda c: c)
    assert sleeps == [9.0]


def test_stats_line(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    llm = LLMPool.from_env()
    with patch(_POST, return_value=_reply()):
        llm.complete("p", lambda c: c)
    assert llm.stats() == "nvidia no key, gemini no key, groq 1 ok"
