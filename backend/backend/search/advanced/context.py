"""Run-scoped context of the advanced-search agent: never checkpointed, rebuilt on every request."""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.search.advanced.llm import AgentLLM


@dataclass
class AgentContext:
    user_id: uuid.UUID
    run_id: str
    cv_text: str  # lives only here, so the CV never lands in the checkpoint tables
    preference_memory: str
    llm: AgentLLM  # the pool's providers in order (search/advanced/llm.py)
    biencoder: Any
    reranker: Any
    os_client: Any
    session_factory: async_sessionmaker
    deadline: float  # a time.monotonic() value
    now: datetime  # timezone-aware; job ages are counted from it
