"""LLM providers behind one OpenAI-compatible interface, tried in order as a pool (`llmpool/pool.py`).

Each provider is marked `private` when its terms say prompts are neither kept nor
used for training. Company enrichment sends only public data and uses every
provider, the private one last to keep its quota; offer summaries, public too,
leave it out entirely (`OFFER_LLM_PROVIDERS`). Anything carrying user data must
build the pool with `private_only=True`.

| provider | free tier                               | private |
|----------|-----------------------------------------|---------|
| nvidia   | ~40 requests/minute                     | no (retention not documented) |
| gemini   | daily request cap                       | no (free tier is used for training, human review) |
| groq     | 8k tokens/minute, 200k tokens/day/model | yes (no training, no retention by default) |
"""
import logging
import os
from collections.abc import Callable
from typing import TypeVar

import requests

from llmpool.pool import Pool, Provider, Unusable, order_from_env

logger = logging.getLogger(__name__)

_TIMEOUT = 45
DEFAULT_ORDER = ("nvidia", "gemini", "groq")
# one call per new job: Groq's daily tokens would not last, and it serves the private calls
OFFER_ORDER = ("nvidia", "gemini")

T = TypeVar("T")


class ChatProvider(Provider):
    base_url: str
    model_env: str
    default_model: str
    private: bool = False
    # an unknown or retired model will not come back this run either
    unusable_statuses = frozenset({401, 402, 403, 404})
    # provider-specific request fields
    extra_body: dict = {}

    def __init__(self, api_key: str | None, model: str | None = None):
        super().__init__(api_key)
        # comma-separated: a model the provider no longer serves (404) gives way to the next
        self.models = [m.strip() for m in (model or self.default_model).split(",") if m.strip()]

    @property
    def model(self) -> str:
        return self.models[0]

    def complete(self, prompt: str) -> str:
        """One chat completion in JSON mode; the message content."""
        while True:
            try:
                resp = self._send(lambda: requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": [{"role": "user", "content": prompt}],
                          "response_format": {"type": "json_object"}, **self.extra_body},
                    timeout=_TIMEOUT,
                ))
            except Unusable as e:
                if not str(e).startswith("HTTP 404") or len(self.models) == 1:
                    raise
                logger.warning("%s does not serve %s any more; trying %s", self.name, self.model, self.models[1])
                self.models.pop(0)
                continue
            return resp.json()["choices"][0]["message"]["content"] or ""


class Nvidia(ChatProvider):
    name = "nvidia"
    env_key = "NVIDIA_NIM_API_KEY"
    base_url = "https://integrate.api.nvidia.com/v1"
    model_env = "NVIDIA_MODEL"
    default_model = "nvidia/nemotron-3-ultra-550b-a55b,nvidia/nemotron-3.5-lightning-30b-a3b"
    min_interval = 1.6
    # Nemotron reasons before answering by default, which takes the free endpoint past the
    # timeout; with thinking off a summary takes ~5 s
    extra_body = {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}}


class Gemini(ChatProvider):
    name = "gemini"
    env_key = "GEMINI_API_KEY"
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    model_env = "GEMINI_MODEL"
    default_model = "gemini-flash-lite-latest"
    min_interval = 4.0


class Groq(ChatProvider):
    name = "groq"
    env_key = "GROQ_API_KEY"
    base_url = "https://api.groq.com/openai/v1"
    model_env = "GROQ_MODEL_BASE"
    default_model = "openai/gpt-oss-20b"
    private = True
    # 8,000 tokens per minute; a company call is ~2k tokens
    min_interval = 12.0


PROVIDERS: dict[str, type[ChatProvider]] = {p.name: p for p in (Nvidia, Gemini, Groq)}


class LLMPool(Pool[ChatProvider]):
    @classmethod
    def from_env(
        cls, private_only: bool = False, order_env: str = "LLM_PROVIDERS", default: tuple[str, ...] = DEFAULT_ORDER,
    ) -> "LLMPool":
        """`order_env` (comma-separated) sets the order; providers without a key are skipped."""
        names = order_from_env(os.environ.get(order_env), default, PROVIDERS)
        providers = [
            PROVIDERS[n](os.environ.get(PROVIDERS[n].env_key), os.environ.get(PROVIDERS[n].model_env))
            for n in names
        ]
        return cls([p for p in providers if p.private or not private_only])

    def complete(
        self, prompt: str, parse: Callable[[str], T], what: str = "LLM call", wait_up_to: float = 0.0,
    ) -> T | None:
        """`parse(content)` of the first provider whose answer parses; an answer that does not
        counts as that provider's failure, and the next one is tried."""
        return self.call(what, lambda p: parse(p.complete(prompt)), wait_up_to)


def offer_pool() -> LLMPool:
    """The pool for offer summaries: `OFFER_LLM_PROVIDERS`, default NVIDIA then Gemini."""
    return LLMPool.from_env(order_env="OFFER_LLM_PROVIDERS", default=OFFER_ORDER)
