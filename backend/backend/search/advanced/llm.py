"""The advanced agent's LLM: the providers of the API's pool (`backend.llm.get_pool`), tried in
`LLM_PROVIDERS` order.

Each call goes to the first usable provider; its failure is booked on the shared provider state
(llmpool's `rate_limited` / `transient` / `unusable`), so a provider resting after a 429 is skipped
by every request of the process, then the next provider is tried:

- 429: paused (Retry-After, else a minute); a daily quota: out for an hour;
- 5xx, connection error, timeout, an answer that does not parse: transient;
- 401/402/403/404: out for an hour (a 404 first gives way to the provider's next model);
- 400 `tool_use_failed` / `json_validate_failed` (Groq: the model's own tool call or JSON did not
  validate): the same provider again, up to `_MALFORMED_RETRIES` more times, then transient;
- 400 saying the request is not supported (tools, `response_format` json_schema, a schema
  feature): that provider's model is skipped for that kind of call by the agent only, for an hour; the
  pool's other users still get it;
- any other 400: transient.

No provider left: `NoProviderAnswered`, so the run ends partial. Every call is cut at its
`timeout` (`TimeoutError`), across all the providers it tries; each attempt gets a share of what
is left (`min(60 s, max(15 s, left / usable providers still to try))`), and an attempt cut while
another usable provider remains is booked transient and the next one gets the rest.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from llmpool.llm import ChatProvider
from llmpool.pool import UNUSABLE_PAUSE, Provider, RateLimited, Transient, Unusable
from pydantic import BaseModel

from backend.llm import get_pool
from backend.search.advanced.budget import DISTILL_SECONDS

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60
# an attempt gets the time left shared among the usable providers still to try, at least this
_MIN_ATTEMPT_SECONDS = 15
# Groq answers 400 with these codes when the model's tool call / JSON fails its own validation
_MALFORMED_CODES = frozenset({"tool_use_failed", "json_validate_failed"})
_MALFORMED_RETRIES = 2
_UNSUPPORTED_MARKERS = ("not supported", "unsupported", "does not support", "invalid schema", "unknown name")


class NoProviderAnswered(Exception):
    pass


def chat_model(provider: ChatProvider) -> BaseChatModel:
    """A ChatOpenAI on the provider's endpoint and current model. No client retries: a failure
    goes to the next provider instead. Only gpt-oss gets `reasoning_effort` (Groq or NVIDIA
    serving it); the other models are left at their default."""
    return ChatOpenAI(
        model=provider.model, api_key=provider.api_key, base_url=provider.base_url,
        extra_body=provider.extra_body or None, max_retries=0, timeout=_REQUEST_TIMEOUT,
        reasoning_effort="low" if "gpt-oss" in provider.model else None,
    )


def _model_name(provider: Provider) -> str:
    return getattr(provider, "model", "")


def _error_text(e: openai.APIStatusError) -> str:
    """The provider's own error message (openai keeps the body's `error` object), else the whole body."""
    body = e.body
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return body["message"]
    return str(body) if body is not None else e.message


class AgentLLM:
    """The agent's chat models, one per provider, tried in the pool's order."""

    def __init__(self, providers: list[Provider], model_for: Callable[[Provider], BaseChatModel]):
        self.providers = providers
        self._model_for = model_for
        self._models: dict[tuple[str, str], BaseChatModel] = {}
        self._unsupported: dict[tuple[str, str, str], float] = {}  # (provider, model, call kind) -> monotonic until

    async def tool_turn(self, messages: list[BaseMessage], tools: list, max_tokens: int, timeout: float) -> AIMessage:
        """One agent turn: at most one tool call (parallel_tool_calls=False), output capped."""
        return await self._call("tools", timeout, lambda m: m.bind_tools(
            tools, parallel_tool_calls=False, max_tokens=max_tokens).ainvoke(messages))

    async def structured(self, messages: list[BaseMessage], schema: type[BaseModel], max_tokens: int,
                         timeout: float) -> Any:
        """A strict json_schema answer parsed into `schema`."""
        return await self._call("structured", timeout, lambda m: m.with_structured_output(
            schema, method="json_schema", strict=True, max_tokens=max_tokens).ainvoke(messages))

    async def text(self, messages: list[BaseMessage], timeout: float) -> str:
        message = await self._call("text", timeout, lambda m: m.ainvoke(messages))
        return message.content if isinstance(message.content, str) else ""

    def _model(self, provider: Provider) -> BaseChatModel:
        key = (provider.name, _model_name(provider))
        if key not in self._models:
            self._models[key] = self._model_for(provider)
        return self._models[key]

    def _skipped(self, provider: Provider, kind: str) -> bool:
        key = (provider.name, _model_name(provider), kind)
        return not provider.usable() or self._unsupported.get(key, 0.0) > time.monotonic()

    async def _call(self, kind: str, timeout: float, call: Callable[[BaseChatModel], Awaitable[Any]]) -> Any:
        deadline = time.monotonic() + timeout
        tried: list[str] = []
        for i, provider in enumerate(self.providers):
            if self._skipped(provider, kind):
                continue
            malformed = 0
            while True:
                if (left := deadline - time.monotonic()) <= 0:
                    raise TimeoutError(f"agent {kind} call out of time")
                # this provider and the usable ones after it each get a share, so a fallback still fits
                others = sum(not self._skipped(p, kind) for p in self.providers[i + 1:])
                attempt = min(left, _REQUEST_TIMEOUT, max(_MIN_ATTEMPT_SECONDS, left / (others + 1)))
                model = _model_name(provider)
                try:
                    result = await asyncio.wait_for(call(self._model(provider)), timeout=attempt)
                except TimeoutError:
                    if not others:
                        raise  # the call's time is spent and nobody is left to try
                    provider.transient()  # too slow for the time left: the next one gets the rest
                    tried.append(f"{provider.name} cut after {attempt:.0f}s")
                    logger.warning("%s: agent %s call cut after %.0fs; next provider", provider.name, kind, attempt)
                    break
                except Exception as e:
                    retry = self._failed(provider, model, kind, e, malformed, tried)
                    if retry == "malformed":
                        malformed += 1
                    if retry:
                        continue
                    break
                provider.succeeded()
                return result
        # Not chained to the last error: its message may quote the prompt (the CV).
        raise NoProviderAnswered(
            f"no LLM provider answered the agent's {kind} call: {'; '.join(tried) or 'none usable'}")

    def _failed(self, provider: Provider, model: str, kind: str, e: Exception, malformed: int,
                tried: list[str]) -> str | None:
        """Books the failure (a line in `tried`) of a call to `model`. Returns "malformed" or "model"
        when the same provider should be tried again (its answer did not validate; another model of
        it), None to go to the next provider.

        Logs names, statuses and codes only: an error body may quote the prompt (the CV)."""
        if isinstance(e, openai.APIStatusError):
            status, code = e.status_code, e.code
            if status == 400 and code in _MALFORMED_CODES:
                if malformed < _MALFORMED_RETRIES:
                    logger.warning("%s: malformed %s answer (%s); trying it again", provider.name, kind, code)
                    return "malformed"
                provider.transient()
                tried.append(f"{provider.name} HTTP 400 {code} x{malformed + 1}")
                logger.warning("%s: malformed %s answer (%s) %d times; next provider",
                               provider.name, kind, code, malformed + 1)
                return None
            text = _error_text(e)
            if status == 400 and any(m in text.lower() for m in _UNSUPPORTED_MARKERS):
                self._unsupported[(provider.name, model, kind)] = time.monotonic() + UNUSABLE_PAUSE
                tried.append(f"{provider.name} does not support it")
                logger.warning("%s (%s) does not support the agent's %s call (HTTP 400); skipped for it",
                               provider.name, model, kind)
                return None
            models = getattr(provider, "models", [])
            if status == 404 and models and models[0] != model:
                return "model"  # a concurrent request already moved past the model that 404'd
            if status == 404 and len(models) > 1:
                logger.warning("%s does not serve %s any more; trying %s", provider.name, models[0], models[1])
                models.pop(0)
                return "model"
            error = provider.error_for(status, text, e.response.headers)
            detail = f"HTTP {status}" + (f" {code}" if code else "")
        else:  # connection error, client timeout, an answer that does not parse
            error = Transient(type(e).__name__)
            detail = type(e).__name__
        if isinstance(error, RateLimited):
            provider.rate_limited(error.retry_after)
        elif isinstance(error, Unusable):
            provider.unusable()
        else:
            provider.transient()
        tried.append(f"{provider.name} {detail}")
        logger.warning("%s failed the agent's %s call (%s, %s); next provider",
                       provider.name, kind, detail, type(error).__name__)
        return None


@cache
def get_agent_llm() -> AgentLLM:
    """The agent's LLM over the API's pool (`LLM_PROVIDERS`; providers without a key are left out).

    The CV the agent reads is the redacted text the user saved, so every pool provider may see it.
    Raises when no provider has a key."""
    pool = get_pool()
    if not pool.configured():
        raise RuntimeError("no LLM provider has a key (LLM_PROVIDERS)")
    return AgentLLM(pool.providers, chat_model)


_DISTILL_SYSTEM = (
    "You maintain a short natural-language summary of a job seeker's preferences, learned from "
    "their searches. Merge the new session signals into the existing summary and return the "
    "updated summary (a few sentences). If the existing summary was written by the user "
    "(user_edited=true), preserve their statements verbatim and only APPEND newly observed "
    "signals. Return ONLY the summary text, no prose wrapper, no markdown."
)


async def distill_memory(existing: str, user_edited: bool, query: str, filters_summary: str,
                         clarify_qa: list[tuple[str, str]]) -> str:
    qa = "\n".join(f"Q: {q} A: {a}" for q, a in clarify_qa) or "(none)"
    text = await get_agent_llm().text([
        SystemMessage(content=_DISTILL_SYSTEM),
        HumanMessage(content=(
            f"Existing summary (user_edited={str(user_edited).lower()}):\n{existing or '(none)'}\n\n"
            f"New session — query: {query}\nfilters: {filters_summary}\nclarifications:\n{qa}"
        )),
    ], timeout=DISTILL_SECONDS)
    return text.strip() or existing  # an empty answer must not overwrite the stored memory
