import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from backend.search.advanced.agent import build_graph
from backend.search.advanced.brief import Brief
from backend.search.advanced.finalize import PARTIAL_SUMMARY, FinalShortlist
from tests.search.advanced.conftest import JOB_A, item
from tests.search.advanced.fakes import ScriptedChatModel, tool_call
from tests.search.advanced.samples import REMOTE

FIRST_SHORT_ID = JOB_A[:4]  # the short id of the first candidate the patched search returns


def brief() -> AIMessage:
    """An empty update_brief: search_jobs is refused before one."""
    return tool_call("update_brief", {"items": []})


def search(query: str = "x") -> AIMessage:
    return tool_call("search_jobs", {"variants": [{"query": query}]})


@pytest.fixture
def graph(patched_run_variant):
    return build_graph(MemorySaver())


@pytest.fixture
def run_variant_empty(patched_run_variant):
    """Every search finds nothing (patched over the canned candidates)."""
    with patch("backend.search.advanced.tools.run_variant", AsyncMock(return_value=[])):
        yield


def initial(query: str) -> dict:
    return {"messages": [HumanMessage(query)], "user_id": str(uuid.uuid4()), "query": query,
            "brief": Brief().model_dump()}


def config(thread_id: str | None = None) -> dict:
    return {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}


async def test_full_run_searches_then_finalizes(graph, ctx_factory):
    llm = ScriptedChatModel(
        script=[tool_call("update_brief", {"items": [REMOTE.model_dump()]}),
                tool_call("search_jobs", {"variants": [{"query": "ml engineer"}]}),
                tool_call("finish", {"keep": [], "notes": "ok"})],
        structured=[FinalShortlist(summary="Two good remote roles.", shortlist=[item(FIRST_SHORT_ID, 88)])])
    out = await graph.ainvoke(initial("remote ml"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["status"] == "done" and out["summary"] == "Two good remote roles."
    assert out["shortlist"][0]["fit_score"] == 88 and out["turns"] == 3


async def test_turn_budget_forces_finalize(graph, ctx_factory):
    llm = ScriptedChatModel(script=[tool_call("inspect_job", {"ids": ["zzzz"]})] * 6,
                            structured=[FinalShortlist(summary="s", shortlist=[])])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["turns"] == 6 and out["status"] == "done"


async def test_deadline_forces_finalize(graph, ctx_factory):
    llm = ScriptedChatModel(script=[], structured=[FinalShortlist(summary="late", shortlist=[])])
    ctx = ctx_factory(llm=llm, deadline=time.monotonic() - 1)
    out = await graph.ainvoke(initial("x"), config(), context=ctx, durability="exit")
    assert (out["status"], out["summary"], out.get("turns", 0)) == ("done", "late", 0)


async def test_plain_text_reply_goes_to_finalize(graph, ctx_factory):
    llm = ScriptedChatModel(script=[AIMessage(content="I think we're done")],
                            structured=[FinalShortlist(summary="s", shortlist=[])])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["status"] == "done"


async def test_unparsed_tool_call_ends_partial(graph, ctx_factory):
    broken = AIMessage(content="", invalid_tool_calls=[
        {"name": "search_jobs", "args": "{not json", "id": "call-x", "error": "bad json", "type": "invalid_tool_call"}])
    llm = ScriptedChatModel(script=[broken], structured=[])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert (out["status"], out["summary"]) == ("partial", PARTIAL_SUMMARY)


async def test_no_hits_ends_done_with_empty_results(graph, ctx_factory, run_variant_empty):
    llm = ScriptedChatModel(script=[brief(), search(),
                                    tool_call("finish", {"keep": [], "notes": "nothing"})],
                            structured=[FinalShortlist(summary="Nothing matches this week.", shortlist=[])])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert (out["status"], out["shortlist"]) == ("done", [])


async def test_llm_error_ends_partial(graph, ctx_factory):
    llm = ScriptedChatModel(script=[brief(), search(), RuntimeError("429")],
                            structured=[])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["status"] == "partial" and out["shortlist"]


async def test_finalize_error_ends_partial(graph, ctx_factory):
    llm = ScriptedChatModel(script=[brief(), search(),
                                    tool_call("finish", {"keep": [], "notes": ""})],
                            structured=[RuntimeError("timeout")])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["status"] == "partial" and [r["job_id"] for r in out["shortlist"]][0] == JOB_A


async def test_tool_raising_runtime_error_is_reported_to_the_agent(graph, ctx_factory):
    llm = ScriptedChatModel(script=[brief(), search(),
                                    tool_call("finish", {"keep": [], "notes": ""})],
                            structured=[FinalShortlist(summary="s", shortlist=[])])
    with patch("backend.search.advanced.tools.render_observation", side_effect=RuntimeError("boom")):
        out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    errors = [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(errors) == 1 and "RuntimeError" in errors[0].content and "boom" in errors[0].content
    assert out["status"] == "done"


async def test_usage_adds_up_every_llm_call(graph, ctx_factory):
    step = tool_call("finish", {"keep": [], "notes": ""})
    step.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    llm = ScriptedChatModel(script=[step], structured=[FinalShortlist(summary="s", shortlist=[])])
    out = await graph.ainvoke(initial("x"), config(), context=ctx_factory(llm=llm), durability="exit")
    assert out["usage"] == {"llm_calls": 2, "input_tokens": 100, "output_tokens": 20}


async def test_agent_sees_system_prompt_with_cv_then_history(graph, ctx_factory):
    llm = ScriptedChatModel(script=[tool_call("finish", {"keep": [], "notes": ""})],
                            structured=[FinalShortlist(summary="s", shortlist=[])])
    await graph.ainvoke(initial("remote ml"), config(), context=ctx_factory(llm=llm, cv_text="CV-TEXT"),
                        durability="exit")
    first = llm.calls[0]
    assert "CV-TEXT" in first[0].content and first[1].content == "remote ml"


async def test_ask_user_pauses_and_resumes(graph, ctx_factory):
    llm = ScriptedChatModel(
        script=[brief(), search(),
                tool_call("ask_user", {"question": "Hybrid ok?", "options": [{"label": "Yes"}, {"label": "No"}]}),
                tool_call("finish", {"keep": [], "notes": ""})],
        structured=[FinalShortlist(summary="s", shortlist=[])])
    ctx = ctx_factory(llm=llm)
    await graph.ainvoke(initial("x"), config("t1"), context=ctx, durability="exit")
    snap = await graph.aget_state(config("t1"))
    assert snap.tasks[0].interrupts[0].value["question"] == "Hybrid ok?"
    out = await graph.ainvoke(Command(resume="No"), config("t1"), context=ctx, durability="exit")
    assert out["status"] == "done"
    assert [{k: q[k] for k in ("question", "answer")} for q in out["qa"]] == [{"question": "Hybrid ok?", "answer": "No"}]


async def test_cv_is_not_in_checkpointed_state(graph, ctx_factory):
    ctx = ctx_factory(cv_text="SECRET-CV-MARKER", llm=ScriptedChatModel(
        script=[brief(), search(), tool_call("ask_user", {"question": "Q?", "options": [{"label": "A"}]})],
        structured=[]))
    await graph.ainvoke(initial("x"), config("t2"), context=ctx, durability="exit")
    snap = await graph.aget_state(config("t2"))
    assert snap.interrupts  # paused at the question, so the state is checkpointed
    assert "SECRET-CV-MARKER" not in repr(snap.values)
