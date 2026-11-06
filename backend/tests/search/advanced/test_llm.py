"""AgentLLM: the pool's providers in order, failures booked on llmpool's provider state."""
import asyncio
import json
import time

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from llmpool.llm import PROVIDERS, Groq, LLMPool
from pydantic import BaseModel

from backend.llm import get_pool
from backend.search.advanced.llm import AgentLLM, NoProviderAnswered, chat_model, get_agent_llm
from tests.search.advanced.fakes import Delayed, ScriptedChatModel, agent_llm, api_error, tool_call

PROMPT = [HumanMessage("find me a job")]
TOOL_USE_FAILED = dict(code="tool_use_failed", message="Failed to call a function. See 'failed_generation'.",
                       failed_generation='{"name": "update_brief", "arguments": {"notes": "SECRET-CV-QUOTE')


@tool
def finish(notes: str) -> str:
    """Ends the run."""
    return notes


class Answer(BaseModel):
    text: str


def scripted(*entries) -> ScriptedChatModel:
    return ScriptedChatModel(script=list(entries))


async def turn(llm: AgentLLM, timeout: float = 30) -> AIMessage:
    return await llm.tool_turn(PROMPT, [finish], 100, timeout=timeout)


async def test_the_first_provider_answers():
    first, second = scripted(tool_call("finish", {"notes": "a"})), scripted()
    llm = agent_llm(first, second)
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "a"}
    assert (len(first.calls), len(second.calls)) == (1, 0)
    assert first.bound_kwargs == {"parallel_tool_calls": False, "max_tokens": 100}
    assert llm.providers[0].ok == 1


async def test_rate_limit_pauses_the_provider_and_the_next_one_answers():
    first = scripted(api_error(429, message="tokens per minute (TPM)", headers={"Retry-After": "30"}))
    second = scripted(tool_call("finish", {"notes": "b"}), tool_call("finish", {"notes": "c"}))
    llm = agent_llm(first, second)
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "b"}
    paused = llm.providers[0]
    assert not paused.usable() and not paused.disabled
    assert 25 < paused.paused_until - time.monotonic() <= 30
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "c"}  # the paused provider is skipped
    assert len(first.calls) == 1


async def test_daily_quota_puts_the_provider_out():
    first = scripted(api_error(429, message="Rate limit reached on tokens per day (TPD)"))
    llm = agent_llm(first, scripted(tool_call("finish", {"notes": "b"})))
    await turn(llm)
    assert llm.providers[0].disabled


async def test_tool_use_failed_is_retried_on_the_same_provider(caplog):
    first = scripted(api_error(400, **TOOL_USE_FAILED), api_error(400, **TOOL_USE_FAILED),
                     tool_call("finish", {"notes": "third time"}))
    second = scripted()
    llm = agent_llm(first, second)
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "third time"}
    assert (len(first.calls), len(second.calls)) == (3, 0)
    assert llm.providers[0].failed == 0 and llm.providers[0].ok == 1
    assert "tool_use_failed" in caplog.text and "SECRET-CV-QUOTE" not in caplog.text


async def test_tool_use_failed_three_times_goes_to_the_next_provider():
    first = scripted(*[api_error(400, **TOOL_USE_FAILED)] * 3)
    llm = agent_llm(first, scripted(tool_call("finish", {"notes": "next"})))
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "next"}
    assert len(first.calls) == 3 and llm.providers[0].failed == 1


async def test_unsupported_tools_skip_the_provider_for_the_agent_only():
    first = ScriptedChatModel(script=[api_error(400, message="tools is not supported with this model")],
                              structured=[Answer(text="structured still works")])
    second = scripted(tool_call("finish", {"notes": "b"}), tool_call("finish", {"notes": "c"}))
    llm = agent_llm(first, second)
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "b"}
    assert llm.providers[0].usable()  # not disabled for the rest of the pool
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "c"}  # skipped for tool turns
    assert len(first.calls) == 1
    assert (await llm.structured(PROMPT, Answer, 100, timeout=30)).text == "structured still works"


async def test_unsupported_json_schema_skips_the_provider_for_structured_calls():
    first = ScriptedChatModel(structured=[api_error(
        400, message="`response_format.type` json_schema is not supported with this model")])
    second = ScriptedChatModel(structured=[Answer(text="from the second")])
    llm = agent_llm(first, second)
    assert (await llm.structured(PROMPT, Answer, 100, timeout=30)).text == "from the second"
    assert first.structured_kwargs == {"method": "json_schema", "strict": True, "max_tokens": 100}


@pytest.mark.parametrize("error", [
    api_error(503, message="overloaded"),
    openai.APIConnectionError(request=httpx.Request("POST", "https://llm.test")),
    openai.APITimeoutError(request=httpx.Request("POST", "https://llm.test")),
    ValueError("an answer that does not parse"),
])
async def test_transient_failures_go_to_the_next_provider(error):
    llm = agent_llm(scripted(error), scripted(tool_call("finish", {"notes": "b"})))
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "b"}
    first = llm.providers[0]
    assert first.failed == 1 and first.usable()  # one transient failure does not pause it


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_bad_key_or_unknown_model_puts_the_provider_out(status):
    llm = agent_llm(scripted(api_error(status)), scripted(tool_call("finish", {"notes": "b"})))
    await turn(llm)
    assert llm.providers[0].disabled


async def test_a_model_no_longer_served_gives_way_to_the_providers_next_model():
    groq = Groq("k", "retired-model,current-model")
    models = {"retired-model": scripted(api_error(404)), "current-model": scripted(tool_call("finish", {"notes": "b"}))}
    llm = AgentLLM([groq], lambda p: models[p.model])
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "b"}
    assert groq.models == ["current-model"] and not groq.disabled


async def test_concurrent_404s_on_the_same_model_switch_it_once():
    groq = Groq("k", "retired-model,current-model")
    retired = scripted(Delayed(0.05, api_error(404)), Delayed(0.05, api_error(404)))
    current = scripted(tool_call("finish", {"notes": "1"}), tool_call("finish", {"notes": "2"}))
    models = {"retired-model": retired, "current-model": current}
    llm = AgentLLM([groq], lambda p: models[p.model])
    answers = await asyncio.gather(turn(llm), turn(llm))  # both in flight on the retired model
    assert sorted(a.tool_calls[0]["args"]["notes"] for a in answers) == ["1", "2"]
    assert groq.models == ["current-model"] and not groq.disabled and len(retired.calls) == 2


async def test_a_model_switch_does_not_use_up_the_malformed_retries():
    groq = Groq("k", "retired-model,current-model")
    current = scripted(api_error(400, **TOOL_USE_FAILED), api_error(400, **TOOL_USE_FAILED),
                       tool_call("finish", {"notes": "third time"}))
    models = {"retired-model": scripted(api_error(404)), "current-model": current}
    llm = AgentLLM([groq], lambda p: models[p.model])
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "third time"}
    assert len(current.calls) == 3


async def test_unsupported_is_remembered_per_model():
    groq = Groq("k", "old-model,new-model")
    models = {"old-model": scripted(api_error(400, message="tools are not supported with this model")),
              "new-model": scripted(tool_call("finish", {"notes": "new"}))}
    llm = AgentLLM([groq], lambda p: models[p.model])
    with pytest.raises(NoProviderAnswered):
        await turn(llm)
    groq.models.pop(0)  # e.g. the old model later answered 404
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "new"}


async def test_a_slow_provider_is_cut_so_the_next_one_still_fits(monkeypatch):
    monkeypatch.setattr("backend.search.advanced.llm._MIN_ATTEMPT_SECONDS", 0.1)
    slow = scripted(Delayed(5, tool_call("finish", {"notes": "late"})))
    llm = agent_llm(slow, scripted(tool_call("finish", {"notes": "in time"})))
    started = time.monotonic()
    assert (await turn(llm, timeout=1.0)).tool_calls[0]["args"] == {"notes": "in time"}  # first gets 0.5 s
    assert time.monotonic() - started < 0.9
    first = llm.providers[0]
    assert first.failed == 1 and first.ok == 0  # booked transient


async def test_an_attempt_share_is_at_least_the_floor():
    llm = agent_llm(scripted(Delayed(0.3, tool_call("finish", {"notes": "slowish"}))), scripted(), scripted())
    # 0.6 s over three providers would be 0.2 s each; the 15 s floor (capped by the time left) lets it finish
    assert (await turn(llm, timeout=0.6)).tool_calls[0]["args"] == {"notes": "slowish"}


async def test_every_provider_failing_raises_without_the_error_bodies():
    llm = agent_llm(scripted(api_error(429)), scripted(*[api_error(400, **TOOL_USE_FAILED)] * 3))
    with pytest.raises(NoProviderAnswered) as raised:
        await turn(llm)
    assert "provider-0 HTTP 429" in str(raised.value) and "provider-1 HTTP 400 tool_use_failed x3" in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None  # the bodies may quote the CV
    with pytest.raises(NoProviderAnswered):  # nothing usable left: no call at all
        await turn(agent_llm())


async def test_the_timeout_spans_every_provider():
    second = scripted(tool_call("finish", {"notes": "never"}))
    llm = agent_llm(scripted(Delayed(5, tool_call("finish", {"notes": "late"}))), second)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await turn(llm, timeout=0.2)
    assert time.monotonic() - started < 1 and len(second.calls) == 0


# The real client: how openai / langchain-openai surface an HTTP error to the classification.

def _completion(arguments: dict) -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "finish", "arguments": json.dumps(arguments)}}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def _real_client(responses: list[httpx.Response], requests: list[dict]) -> ChatOpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return responses.pop(0)

    return ChatOpenAI(model="openai/gpt-oss-120b", api_key="k", base_url="https://llm.test/v1", max_retries=0,
                      reasoning_effort="low", http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_groq_tool_use_failed_through_the_real_client_is_retried():
    requests: list[dict] = []
    groq_400 = {"error": {"message": "Failed to call a function.", "type": "invalid_request_error",
                          "code": "tool_use_failed", "failed_generation": "<|call|>{broken"}}
    model = _real_client([httpx.Response(400, json=groq_400), httpx.Response(200, json=_completion({"notes": "ok"}))],
                         requests)
    llm = agent_llm(model)
    assert (await turn(llm)).tool_calls[0]["args"] == {"notes": "ok"}
    assert len(requests) == 2
    body = requests[0]
    # langchain-openai sends the cap as max_completion_tokens
    assert (body["parallel_tool_calls"], body["max_completion_tokens"], body["reasoning_effort"]) == (False, 100, "low")


async def test_retry_after_is_read_from_the_real_response():
    model = _real_client([httpx.Response(429, headers={"retry-after": "12"},
                                         json={"error": {"message": "TPM", "code": "rate_limit_exceeded"}})], [])
    llm = agent_llm(model, scripted(tool_call("finish", {"notes": "b"})))
    await turn(llm)
    assert 8 < llm.providers[0].paused_until - time.monotonic() <= 12


# Production wiring: providers and their settings come from llmpool.

@pytest.fixture
def pool_env(monkeypatch):
    for cls in PROVIDERS.values():
        monkeypatch.delenv(cls.env_key, raising=False)
        monkeypatch.delenv(cls.model_env, raising=False)
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    get_pool.cache_clear()
    get_agent_llm.cache_clear()
    yield monkeypatch
    get_pool.cache_clear()
    get_agent_llm.cache_clear()


def test_agent_llm_follows_the_pool_order_and_provider_settings(pool_env):
    pool_env.setenv("LLM_PROVIDERS", "groq,nvidia,gemini")
    pool_env.setenv("GROQ_MODEL_LARGE", "openai/gpt-oss-120b")
    for key in ("GROQ_API_KEY", "NVIDIA_NIM_API_KEY"):
        pool_env.setenv(key, f"key-{key}")
    llm = get_agent_llm()
    assert llm.providers is get_pool().providers  # shared pause state
    assert [p.name for p in llm.providers if p.api_key] == ["groq", "nvidia"]
    groq, nvidia = (chat_model(p) for p in llm.providers[:2])
    assert (groq.model_name, groq.openai_api_key.get_secret_value(), groq.openai_api_base) == (
        "openai/gpt-oss-120b", "key-GROQ_API_KEY", PROVIDERS["groq"].base_url)
    assert (groq.reasoning_effort, groq.extra_body, groq.max_retries, groq.request_timeout) == ("low", None, 0, 60)
    assert nvidia.model_name == PROVIDERS["nvidia"].default_model.split(",")[0]
    assert (nvidia.reasoning_effort, nvidia.extra_body) == (None, PROVIDERS["nvidia"].extra_body)


def test_no_provider_key_raises(pool_env):
    with pytest.raises(RuntimeError):
        get_agent_llm()


def test_gemini_gets_no_reasoning_effort(pool_env):
    pool_env.setenv("GEMINI_API_KEY", "k")
    gemini = LLMPool.from_env(default=("gemini",)).providers[0]
    assert chat_model(gemini).reasoning_effort is None
