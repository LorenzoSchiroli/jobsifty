import os
from functools import cache

from llmpool.llm import LLMPool


@cache
def get_pool() -> LLMPool:
    """The one LLM pool of the API (order from LLM_PROVIDERS, Groq on the large model).

    Unpaced: a client-side sleep would be shared by every user of the API.
    """
    return LLMPool.from_env(pace=False, models={"groq": os.environ["GROQ_MODEL_LARGE"]})
