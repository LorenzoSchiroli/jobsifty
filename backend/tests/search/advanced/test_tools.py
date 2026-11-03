import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from backend.search.advanced.brief import Brief
from backend.search.advanced.budget import INSPECT_CHARS, MAX_PROBES, MAX_SEARCHES
from backend.search.advanced.state import add_usage, merge_dicts, merge_pool
from backend.search.advanced.tools import ONE_CALL_REFUSAL
from tests.search.advanced.conftest import JOB_A, JOB_B
from tests.search.advanced.fakes import ScriptedChatModel, tool_call
from tests.search.advanced.samples import IN_MILAN, REMOTE, STARTUP, candidate


# --- update_brief -----------------------------------------------------------------------------

async def test_update_brief_refuses_dropping_stated_item(run_tool):
    out = await run_tool("update_brief", {"items": []}, state={"brief": Brief(items=[REMOTE]).model_dump(), "turns": 2})
    assert "only the user can relax it" in out.messages[-1].content
    assert "'remote only'" in out.messages[-1].content
    assert Brief(**out.state["brief"]).items == [REMOTE]


async def test_update_brief_allows_unlock_right_after_an_answer(run_tool):
    out = await run_tool("update_brief", {"items": []},
                         state={"brief": Brief(items=[REMOTE]).model_dump(), "turns": 3, "unlock_turn": 2})
    assert out.messages[-1].content == "ok"
    assert Brief(**out.state["brief"]).items == []


async def test_update_brief_stores_brief_and_streams_it(run_tool):
    out = await run_tool("update_brief", {"items": [REMOTE.model_dump(), STARTUP.model_dump()], "notes": "PyTorch"})
    brief = Brief(**out.state["brief"])
    assert brief.items == [REMOTE, STARTUP] and brief.notes == "PyTorch"
    assert out.events == [{"type": "brief", "brief": brief.to_api()}]
    assert isinstance(out.messages[-1], ToolMessage) and out.messages[-1].tool_call_id == "call-1"


async def test_update_brief_invalid_item_comes_back_as_readable_error(run_tool):
    out = await run_tool("update_brief", {"items": [{"text": "remote", "kind": "require", "source": "stated",
                                                     "field": "location_type"}]})
    assert out.messages[-1].status == "error"
    assert "field and value must be set together" in out.messages[-1].content
    assert "brief" not in out.state


# --- search_jobs ------------------------------------------------------------------------------

async def test_search_rejects_too_many_variants(run_tool):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}] * 4})
    assert "at most 3" in out.messages[-1].content


async def test_search_adds_to_pool_and_logs(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}]})
    assert len(out.state["pool"]) == 2 and out.state["searches_run"] == 1
    assert out.state["searches"][0]["query"] == "ml"


async def test_search_log_entry_short_ids_and_stream(run_tool, patched_run_variant):
    out = await run_tool(
        "search_jobs",
        {"variants": [{"query": "ml", "filters": {"location_type": "remote"}, "max_age_hours": 168}]},
        state={"brief": Brief(items=[REMOTE]).model_dump(), "searches": [{"index": 1}], "searches_run": 1},
    )
    [_, entry] = out.state["searches"]
    assert entry["index"] == 2 and entry["probe"] is None and entry["max_age_hours"] == 168
    assert entry["filters"] == {"location_type": "remote", "locked": {"location_type": "remote"}}
    assert entry["matches"] == 1  # only JOB_A is remote
    assert entry["header"].startswith('search 2 "ml" · locked: location_type=remote')
    assert out.state["short_ids"] == {"a1f3": JOB_A, "9c02": JOB_B}
    assert out.state["searches_run"] == 2
    assert out.events == [{"type": "search", "line": entry["header"]}]
    assert "[a1f3] ML Engineer" in out.messages[-1].content
    assert patched_run_variant.call_args.kwargs["search_index"] == 2


async def test_search_runs_variants_up_to_budget_and_refuses_the_rest(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b"}]},
                         state={"searches_run": MAX_SEARCHES - 1})
    assert patched_run_variant.await_count == 1
    assert out.state["searches_run"] == MAX_SEARCHES
    assert [s["query"] for s in out.state["searches"]] == ["a"]
    assert 'Refused "b": search budget' in out.messages[-1].content


async def test_probe_counts_and_is_refused_past_probe_budget(run_tool, patched_run_variant):
    brief = Brief(items=[REMOTE]).model_dump()
    out = await run_tool("search_jobs", {"variants": [{"query": "ml", "probe": "remote only"}]},
                         state={"brief": brief, "probes_run": MAX_PROBES - 1})
    assert out.state["probes_run"] == MAX_PROBES and out.state["searches"][0]["probe"] == "remote only"
    out = await run_tool("search_jobs", {"variants": [{"query": "ml", "probe": "remote only"}]},
                         state={"brief": brief, "probes_run": MAX_PROBES})
    assert patched_run_variant.await_count == 1
    assert "probe budget" in out.messages[-1].content and out.state["probes_run"] == MAX_PROBES


async def test_probe_of_inferred_item_is_refused(run_tool):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml", "probe": "startup"}]},
                         state={"brief": Brief(items=[STARTUP]).model_dump()})
    assert "not a stated requirement" in out.messages[-1].content


async def test_probe_of_unmapped_stated_item_is_refused(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml", "probe": "in Milan"}]},
                         state={"brief": Brief(items=[IN_MILAN]).model_dump()})
    assert "maps to no index field" in out.messages[-1].content
    assert patched_run_variant.await_count == 0
    assert out.state.get("searches_run", 0) == 0 and out.state.get("probes_run", 0) == 0


async def test_probe_of_stated_preference_is_refused(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml", "probe": "startup"}]},
                         state={"brief": Brief(items=[STARTUP.model_copy(update={"source": "stated"})]).model_dump()})
    assert "a stated preference, not a hard filter" in out.messages[-1].content
    assert patched_run_variant.await_count == 0


async def test_search_refuses_empty_variants(run_tool):
    out = await run_tool("search_jobs", {"variants": []})
    assert "give 1 to 3 variants" in out.messages[-1].content


async def test_value_error_inside_a_search_is_logged_as_a_failure(run_tool, patched_run_variant, caplog):
    patched_run_variant.side_effect = ValueError("bad vector")
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}]})
    assert 'Search "ml" failed: ValueError: bad vector' in out.messages[-1].content
    assert "advanced search variant failed" in caplog.text


async def test_failed_variant_is_reported_and_the_others_still_run(run_tool, patched_run_variant):
    ok = patched_run_variant.side_effect

    def first_fails(**kw):
        if kw["variant"].query == "a":
            raise RuntimeError("opensearch down")
        return ok(**kw)

    patched_run_variant.side_effect = first_fails
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b"}]})
    assert 'Search "a" failed: RuntimeError: opensearch down' in out.messages[-1].content
    assert [s["query"] for s in out.state["searches"]] == ["b"] and out.state["searches"][0]["index"] == 1
    assert out.state["searches_run"] == 1


async def test_search_with_invalid_variant_comes_back_as_readable_error(run_tool):
    out = await run_tool("search_jobs", {"variants": [{"filters": {}}]})
    assert out.messages[-1].status == "error" and "query" in out.messages[-1].content


# --- pool reducer -----------------------------------------------------------------------------

def test_merge_pool_keeps_higher_score_and_clears_breaks_when_a_normal_search_found_it():
    left = {JOB_A: candidate(JOB_A, 0.5, breaks=["remote only"]).to_dict()}
    right = {JOB_A: candidate(JOB_A, 0.8, search_index=2).to_dict(), JOB_B: candidate(JOB_B).to_dict()}
    merged = merge_pool(left, right)
    assert merged[JOB_A]["score"] == 0.8 and merged[JOB_A]["breaks"] == []
    assert set(merged) == {JOB_A, JOB_B}


def test_merge_pool_unions_breaks_of_two_probes():
    left = {JOB_A: candidate(JOB_A, 0.9, breaks=["remote only"]).to_dict()}
    right = {JOB_A: candidate(JOB_A, 0.4, breaks=["no consulting"], search_index=2).to_dict()}
    merged = merge_pool(left, right)
    assert merged[JOB_A]["score"] == 0.9 and merged[JOB_A]["search_index"] == 1
    assert merged[JOB_A]["breaks"] == ["remote only", "no consulting"]


def test_merge_dicts_and_add_usage():
    assert merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}
    assert add_usage({"llm_calls": 1, "input_tokens": 10, "output_tokens": 2},
                     {"llm_calls": 1, "input_tokens": 5}) == {"llm_calls": 2, "input_tokens": 15, "output_tokens": 2}


# --- inspect_job ------------------------------------------------------------------------------

async def test_inspect_unknown_id_returns_error_message(run_tool):
    out = await run_tool("inspect_job", {"ids": ["zzzz"]})
    assert "Unknown id: zzzz" in out.messages[-1].content


async def test_inspect_known_id_shows_title_company_and_cut_text(run_tool):
    pool = {JOB_A: candidate(JOB_A, text="x" * 1000, company="Acme").to_dict()}
    out = await run_tool("inspect_job", {"ids": ["a1f3", "zzzz"]}, state={"pool": pool, "short_ids": {"a1f3": JOB_A}})
    content = out.messages[-1].content
    assert content.startswith("[a1f3] ML Engineer · Acme\n" + "x" * INSPECT_CHARS + "\n")
    assert "x" * (INSPECT_CHARS + 1) not in content and "Unknown id: zzzz" in content
    assert out.events == [{"type": "inspect", "ids": ["a1f3"]}]


async def test_inspect_refuses_more_than_three(run_tool):
    out = await run_tool("inspect_job", {"ids": ["a", "b", "c", "d"]})
    assert "at most 3" in out.messages[-1].content


# --- ask_user ---------------------------------------------------------------------------------

async def test_ask_user_pauses_then_applies_chosen_option(run_tool_with_resume):
    out = await run_tool_with_resume(
        "ask_user", {"question": "Allow hybrid?", "options": [{"label": "Allow hybrid", "relax": ["remote only"]},
                                                              {"label": "Keep remote only"}]},
        state={"brief": Brief(items=[REMOTE]).model_dump(), "turns": 2}, answer="Allow hybrid")
    assert out.interrupt_value == {"question": "Allow hybrid?", "options": ["Allow hybrid", "Keep remote only"]}
    assert Brief(**out.state["brief"]).items[0].source == "inferred"
    assert out.state["questions_asked"] == 1
    assert out.messages[-1].content == "User chose: Allow hybrid"
    assert out.events == [{"type": "brief", "brief": Brief(**out.state["brief"]).to_api()}]
    assert out.state["qa"] == [{"question": "Allow hybrid?", "options": ["Allow hybrid", "Keep remote only"],
                                "answer": "Allow hybrid", "chosen": "Allow hybrid"}]
    assert "unlock_turn" not in out.state


async def test_ask_user_free_text_sets_unlock_turn(run_tool_with_resume):
    out = await run_tool_with_resume("ask_user", {"question": "Q?", "options": [{"label": "A"}]},
                                     state={"turns": 4}, answer="hybrid is fine if in Milan")
    assert out.state["unlock_turn"] == 4
    assert out.messages[-1].content == "User answered: hybrid is fine if in Milan"
    assert out.state["qa"][0]["chosen"] is None


async def test_ask_user_past_budget_does_not_interrupt(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": []}, state={"questions_asked": 2})
    assert out.interrupt_value is None and "No more questions" in out.messages[-1].content


async def test_ask_user_without_options_is_refused_without_interrupting(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": []})
    assert out.interrupt_value is None and "needs options" in out.messages[-1].content


async def test_ask_user_relaxing_an_unknown_item_is_refused_without_interrupting(run_tool):
    out = await run_tool("ask_user", {"question": "Allow hybrid?",
                                      "options": [{"label": "Allow hybrid", "relax": ["remote"]}, {"label": "Keep"}]},
                         state={"brief": Brief(items=[REMOTE]).model_dump()})
    assert out.interrupt_value is None
    assert "'remote'" in out.messages[-1].content and "not a stated item" in out.messages[-1].content


async def test_ask_user_dict_resume_value_uses_its_answer(run_tool_with_resume):
    out = await run_tool_with_resume("ask_user", {"question": "Q?", "options": [{"label": "A"}]},
                                     answer={"answer": "A"})
    assert out.messages[-1].content == "User chose: A"


async def test_ask_user_has_no_side_effects_before_the_answer(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": [{"label": "A"}]}, state={"turns": 1})
    assert out.interrupt_value == {"question": "Q?", "options": ["A"]}
    assert out.state.get("questions_asked", 0) == 0 and out.state.get("qa", []) == []
    assert isinstance(out.messages[-1], AIMessage)


# --- finish -----------------------------------------------------------------------------------

async def test_finish_resolves_short_ids_and_drops_unknown(run_tool):
    out = await run_tool("finish", {"keep": ["9c02", "zzzz", "a1f3", "9c02"], "notes": "remote is thin"},
                         state={"short_ids": {"a1f3": JOB_A, "9c02": JOB_B}})
    assert out.state["keep"] == [JOB_B, JOB_A]
    assert out.state["notes"] == "remote is thin" and out.state["finished"] is True
    assert "zzzz" in out.messages[-1].content


# --- one tool call per turn ---------------------------------------------------------------------

async def test_second_update_brief_in_one_turn_is_refused(run_tool):
    calls = [("update_brief", {"items": [REMOTE.model_dump()]}), ("update_brief", {"items": [STARTUP.model_dump()]})]
    out = await run_tool(calls=calls)
    first, second = out.messages[-2:]
    assert (first.tool_call_id, first.content) == ("call-1", "ok")
    assert second.tool_call_id == "call-2" and second.status == "error" and second.name == "update_brief"
    assert second.content == ONE_CALL_REFUSAL
    assert Brief(**out.state["brief"]).items == [REMOTE]


async def test_extra_call_is_refused_when_the_first_returns_plain_text(run_tool):
    out = await run_tool(calls=[("inspect_job", {"ids": ["a", "b", "c", "d"]}), ("finish", {"keep": [], "notes": ""})])
    first, second = out.messages[-2:]
    assert first.tool_call_id == "call-1" and "at most 3" in first.content
    assert (second.tool_call_id, second.content) == ("call-2", ONE_CALL_REFUSAL)
    assert "finished" not in out.state


async def test_two_probe_searches_in_one_turn_stay_within_probe_budget(run_tool, patched_run_variant):
    probe = {"variants": [{"query": "ml", "probe": "remote only"}]}
    out = await run_tool(calls=[("search_jobs", probe), ("search_jobs", probe)],
                         state={"brief": Brief(items=[REMOTE]).model_dump(), "probes_run": MAX_PROBES - 1})
    assert out.state["probes_run"] == MAX_PROBES and patched_run_variant.await_count == 1
    assert [m.content for m in out.messages if isinstance(m, ToolMessage)].count(ONE_CALL_REFUSAL) == 1


# --- ScriptedChatModel --------------------------------------------------------------------------

class _Verdict(BaseModel):
    ok: bool


async def test_scripted_model_pops_script_records_calls_and_raises():
    call = tool_call("finish", {"keep": [], "notes": ""})
    model = ScriptedChatModel(script=[call, RuntimeError("down")], structured=[_Verdict(ok=True)])
    assert model.bind_tools([]) is model
    reply = await model.ainvoke([HumanMessage("hi")])
    assert reply.tool_calls[0]["name"] == "finish" and reply.tool_calls[0]["id"]
    with pytest.raises(RuntimeError):
        await model.ainvoke([HumanMessage("again")])
    assert await model.with_structured_output(_Verdict).ainvoke([HumanMessage("judge")]) == _Verdict(ok=True)
    assert [[m.content for m in c] for c in model.calls] == [["hi"], ["again"], ["judge"]]
