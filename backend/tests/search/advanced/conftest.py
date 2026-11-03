"""Shared fixtures for the advanced-search agent: a run context of mocks and one-tool graph runs."""
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from backend.search.advanced.context import AgentContext
from backend.search.advanced.state import AgentState
from backend.search.advanced.tools import tools_step
from tests.search.advanced.fakes import ScriptedChatModel
from tests.search.advanced.samples import candidate

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
JOB_A = "a1f30000-0000-4000-8000-000000000001"
JOB_B = "9c020000-0000-4000-8000-000000000002"


@pytest.fixture
def ctx_factory():
    """Builds an AgentContext of mocks; keyword arguments override any field."""

    def build(**overrides) -> AgentContext:
        values = dict(
            user_id=uuid.uuid4(), run_id="run-1", cv_text="ML engineer, 5 years of PyTorch.",
            preference_memory="", llm=ScriptedChatModel(), biencoder=MagicMock(), reranker=MagicMock(),
            os_client=MagicMock(), session_factory=MagicMock(), deadline=time.monotonic() + 240, now=NOW,
        ) | overrides
        return AgentContext(**values)

    return build


@dataclass
class ToolRun:
    messages: list[AnyMessage]
    state: dict
    interrupt_value: object | None
    events: list[dict] = field(default_factory=list)


def _graph():
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("tools", tools_step)
    builder.set_entry_point("tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=MemorySaver())


async def _stream(graph, payload, config, context, events) -> None:
    async for mode, chunk in graph.astream(payload, config, context=context, stream_mode=["custom", "values"]):
        if mode == "custom":
            events.append(chunk)


async def _result(graph, config, events, interrupt_value=None) -> ToolRun:
    snap = await graph.aget_state(config)
    if interrupt_value is None and snap.interrupts:
        interrupt_value = snap.interrupts[0].value
    return ToolRun(messages=snap.values.get("messages", []), state=snap.values,
                   interrupt_value=interrupt_value, events=events)


def _start(calls: list[tuple[str, dict]], state: dict | None) -> dict:
    """The state with one AIMessage carrying the calls, with ids call-1, call-2, ..."""
    message = AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"} for i, (name, args) in enumerate(calls, 1)
    ])
    return {**(state or {}), "messages": [HumanMessage("find me ML jobs"), message]}


@pytest.fixture
def run_tool(ctx_factory):
    """Runs a tool call through a one-node graph of the tools step: messages, state, interrupt value, events.

    `calls=[(name, args), ...]` puts several calls in the one AIMessage instead.
    """

    async def run(name: str | None = None, args: dict | None = None, state: dict | None = None,
                  context: AgentContext | None = None, *, calls: list[tuple[str, dict]] | None = None) -> ToolRun:
        graph, config, events = _graph(), {"configurable": {"thread_id": str(uuid.uuid4())}}, []
        await _stream(graph, _start(calls or [(name, args)], state), config, context or ctx_factory(), events)
        return await _result(graph, config, events)

    return run


@pytest.fixture
def run_tool_with_resume(ctx_factory):
    """Like run_tool, then resumes the pause with `answer`; interrupt_value is the first pause's value."""

    async def run(name: str, args: dict, *, answer, state: dict | None = None,
                  context: AgentContext | None = None) -> ToolRun:
        graph, config, events = _graph(), {"configurable": {"thread_id": str(uuid.uuid4())}}, []
        ctx = context or ctx_factory()
        await _stream(graph, _start([(name, args)], state), config, ctx, events)
        paused = await graph.aget_state(config)
        value = paused.interrupts[0].value if paused.interrupts else None
        await _stream(graph, Command(resume=answer), config, ctx, events)
        return await _result(graph, config, events, interrupt_value=value)

    return run


@pytest.fixture
def patched_run_variant():
    """tools.run_variant returns the same two candidates (JOB_A remote, JOB_B hybrid) for every variant."""
    mock = AsyncMock(side_effect=lambda **kw: [
        candidate(JOB_A, 0.91, search_index=kw["search_index"]),
        candidate(JOB_B, 0.88, search_index=kw["search_index"], location_type="hybrid"),
    ])
    with patch("backend.search.advanced.tools.run_variant", mock):
        yield mock


JOB_C = "b2c40000-0000-4000-8000-000000000003"
PROBE_JOB_ID = "d4e50000-0000-4000-8000-000000000004"


def item(short: str, score: int):
    from backend.search.advanced.finalize import ShortlistItem

    return ShortlistItem(job_id=short, fit_score=score, fits="fits", trade_offs=[], gaps=[])


@pytest.fixture
def pool_state() -> dict:
    """Brief: remote only. JOB_A is remote, JOB_C on-site (violates the brief)."""
    from backend.search.advanced.brief import Brief
    from tests.search.advanced.samples import REMOTE

    return {
        "brief": Brief(items=[REMOTE]).model_dump(),
        "pool": {
            JOB_A: candidate(JOB_A, 0.91).to_dict(),
            JOB_C: candidate(JOB_C, 0.95, location_type="onsite").to_dict(),
        },
        "short_ids": {"a1f3": JOB_A, "b2c4": JOB_C},
        "searches": [{"index": 1, "header": 'search 1 "ml"',
                      "filters": {"seniority": "senior", "locked": {"location_type": "remote"}}}],
    }


def _with_probe(brief) -> dict:
    return {
        "brief": brief.model_dump(),
        "pool": {
            JOB_A: candidate(JOB_A, 0.91).to_dict(),
            PROBE_JOB_ID: candidate(PROBE_JOB_ID, 0.99, breaks=["in Milan"]).to_dict(),
        },
        "short_ids": {"a1f3": JOB_A, "d4e5": PROBE_JOB_ID},
    }


@pytest.fixture
def pool_state_with_probe() -> dict:
    """A probe job breaking the still-stated "in Milan"."""
    from backend.search.advanced.brief import Brief
    from tests.search.advanced.samples import IN_MILAN, REMOTE

    return _with_probe(Brief(items=[REMOTE, IN_MILAN]))


@pytest.fixture
def pool_state_with_probe_relaxed() -> dict:
    """The same pool after the user relaxed "in Milan"."""
    from backend.search.advanced.brief import Brief, relax
    from tests.search.advanced.samples import IN_MILAN, REMOTE

    return _with_probe(relax(Brief(items=[REMOTE, IN_MILAN]), ["in Milan"]))
