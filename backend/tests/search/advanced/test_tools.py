import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from backend.search.advanced.brief import Brief
from backend.search.advanced.budget import INSPECT_CHARS, MAX_SEARCHES
from backend.search.advanced.state import add_usage, merge_dicts, merge_pool
from backend.search.advanced.tools import ONE_CALL_REFUSAL
from tests.search.advanced.conftest import JOB_A, JOB_B
from tests.search.advanced.fakes import ScriptedChatModel, tool_call
from tests.search.advanced.samples import IN_MILAN, MILAN, REMOTE, STARTUP, candidate

WROTE = {"brief_written": True}  # update_brief has run, so search_jobs may
SEARCHED = {"searches_run": 1}  # a search has run, so ask_user may


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
    assert out.events == [{"status": "Noting what you asked for"}]
    assert isinstance(out.messages[-1], ToolMessage) and out.messages[-1].tool_call_id == "call-1"


@pytest.mark.parametrize("text", [
    "junior remote NLP engineer in Munich, no consulting",  # a list
    "NLP role; Munich",
    "a junior NLP or search engineer role based in Munich",  # more than 8 words
    "Junior remote NLP engineer Munich",  # the whole request
])
async def test_update_brief_refuses_an_item_holding_several_requirements(run_tool, text):
    lumped = {"text": text, "kind": "require", "source": "stated"}
    out = await run_tool("update_brief", {"items": [REMOTE.model_dump(), lumped]},
                         state={"query": "junior remote NLP engineer Munich"})
    assert out.messages[-1].content.startswith(f"Refused: '{text}' holds several requirements")
    assert "brief" not in out.state


async def test_update_brief_takes_one_requirement_per_item(run_tool):
    items = [{"text": t, "kind": "require", "source": "stated"} for t in ("NLP or search engineer role", "in Munich")]
    out = await run_tool("update_brief", {"items": items}, state={"query": "NLP engineer"})  # a two-word request
    assert out.messages[-1].content == "ok"
    out = await run_tool("update_brief", {"items": [{"text": "NLP engineer", "kind": "require", "source": "stated"}]},
                         state={"query": "NLP engineer"})
    assert out.messages[-1].content == "ok"


async def test_update_brief_invalid_item_comes_back_as_readable_error(run_tool):
    out = await run_tool("update_brief", {"items": [{"text": "remote", "kind": "require", "source": "stated",
                                                     "field": "location_type"}]})
    assert out.messages[-1].status == "error"
    assert "field and value must be set together" in out.messages[-1].content
    assert "brief" not in out.state


# --- search_jobs ------------------------------------------------------------------------------

async def test_search_before_update_brief_is_refused(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}]})
    assert out.messages[-1].content.startswith("Refused: call update_brief first")
    assert patched_run_variant.await_count == 0 and out.state.get("searches_run", 0) == 0


async def test_update_brief_marks_the_brief_written(run_tool):
    out = await run_tool("update_brief", {"items": []})
    assert out.state["brief_written"] is True


async def test_search_rejects_too_many_variants(run_tool):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}] * 4}, state=WROTE)
    assert "at most 3" in out.messages[-1].content


async def test_search_adds_to_pool_and_logs(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}]}, state=WROTE)
    assert len(out.state["pool"]) == 2 and out.state["searches_run"] == 1
    assert out.state["searches"][0]["query"] == "ml"


async def test_search_log_entry_short_ids_and_stream(run_tool, patched_run_variant):
    out = await run_tool(
        "search_jobs",
        {"variants": [{"query": "ml", "filters": {"location_type": "remote"}, "max_age_hours": 168}]},
        state={"brief": Brief(items=[REMOTE]).model_dump(), "searches": [{"index": 1}], "searches_run": 1, **WROTE},
    )
    [_, entry] = out.state["searches"]
    assert entry["index"] == 2 and entry["loosened"] == [] and entry["max_age_hours"] == 168
    assert entry["filters"] == {"location_type": "remote", "locked": {"location_type": "remote"}}
    assert entry["matches"] == 1  # only JOB_A is remote
    assert entry["new_jobs"] == 2
    assert entry["header"].startswith('search 2 "ml" · locked: location_type=remote')
    assert out.state["short_ids"] == {"a1f3": JOB_A, "9c02": JOB_B}
    assert out.state["searches_run"] == 2
    assert out.events == [{"status": 'Searching "ml" · last 7 days'}]
    assert "[a1f3] ML Engineer" in out.messages[-1].content
    assert patched_run_variant.call_args.kwargs["search_index"] == 2


async def test_search_log_counts_only_jobs_new_to_the_pool(run_tool, patched_run_variant):
    seen = {JOB_A: candidate(JOB_A, 0.9, search_index=1).to_dict()}
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}, {"query": "ai"}]},
                         state={"pool": seen, "short_ids": {"a1f3": JOB_A}, **WROTE})
    first, second = out.state["searches"]
    assert (first["new_jobs"], second["new_jobs"]) == (1, 0)  # JOB_B is new once, JOB_A was already found
    assert first["matches"] is None  # no soft filters: no match count


async def test_search_runs_variants_up_to_budget_and_refuses_the_rest(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b"}]},
                         state={"searches_run": MAX_SEARCHES - 1, **WROTE})
    assert patched_run_variant.await_count == 1
    assert out.state["searches_run"] == MAX_SEARCHES
    assert [s["query"] for s in out.state["searches"]] == ["a"]
    assert 'Refused "b": search budget' in out.messages[-1].content


async def test_a_loosened_search_logs_what_it_eased_and_counts_the_strict_jobs(run_tool, patched_run_variant):
    brief = Brief(items=[REMOTE, MILAN]).model_dump()
    variant = {"query": "ml", "loosen": [{"field": "place", "value": ["Milano", "Austria"]}, {"field": "location_type"}]}
    out = await run_tool("search_jobs", {"variants": [variant]}, state={"brief": brief, **WROTE})
    [entry] = out.state["searches"]
    assert entry["loosened"] == ["in Milan → in Milan or Austria", "without remote"]
    assert entry["filters"]["locked"] == {"place": ["Milan", "Austria"]}
    assert "· loosened: in Milan → in Milan or Austria; without remote" in entry["header"]
    assert "meeting every stated requirement: 0 of 2" in out.messages[-1].content  # both jobs are in Berlin
    assert out.events == [{"status": 'Searching "ml" · loosened · last 3 days'}]
    assert out.state["searches_run"] == 1  # a loosened search counts like any other


async def test_loosening_what_is_not_stated_is_refused(run_tool, patched_run_variant):
    for brief in (Brief(items=[STARTUP]), Brief(items=[IN_MILAN])):  # an inferred item; a stated one with no field
        field = "is_startup" if brief.items[0].field else "place"
        out = await run_tool("search_jobs", {"variants": [{"query": "ml", "loosen": [{"field": field}]}]},
                             state={"brief": brief.model_dump(), **WROTE})
        assert f"nothing stated on {field} to loosen" in out.messages[-1].content
    assert patched_run_variant.await_count == 0


async def test_preference_is_never_locked_so_it_can_be_dropped(run_tool):
    item = {"text": "preferably remote", "kind": "prefer", "source": "stated", "field": "location_type",
            "value": "remote"}
    out = await run_tool("update_brief", {"items": [item]})
    brief = Brief.model_validate(out.state["brief"])
    assert [(i.kind, i.source) for i in brief.items] == [("prefer", "inferred")]
    dropped = await run_tool("update_brief", {"items": []}, state={"brief": brief.model_dump(), "turns": 2})
    assert Brief.model_validate(dropped.state["brief"]).items == []


async def test_search_refuses_empty_variants(run_tool):
    out = await run_tool("search_jobs", {"variants": []}, state=WROTE)
    assert "give 1 to 3 variants" in out.messages[-1].content


async def test_value_error_inside_a_search_is_logged_as_a_failure(run_tool, patched_run_variant, caplog):
    patched_run_variant.side_effect = ValueError("bad vector")
    out = await run_tool("search_jobs", {"variants": [{"query": "ml"}]}, state=WROTE)
    assert 'Search "ml" failed: ValueError: bad vector' in out.messages[-1].content
    assert "advanced search variant failed" in caplog.text


async def test_failed_variant_is_reported_and_the_others_still_run(run_tool, patched_run_variant):
    ok = patched_run_variant.side_effect

    def first_fails(**kw):
        if kw["variant"].query == "a":
            raise RuntimeError("opensearch down")
        return ok(**kw)

    patched_run_variant.side_effect = first_fails
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b"}]}, state=WROTE)
    assert 'Search "a" failed: RuntimeError: opensearch down' in out.messages[-1].content
    assert [s["query"] for s in out.state["searches"]] == ["b"] and out.state["searches"][0]["index"] == 1
    assert out.state["searches_run"] == 1


async def test_search_with_invalid_variant_comes_back_as_readable_error(run_tool):
    out = await run_tool("search_jobs", {"variants": [{"filters": {}}]})
    assert out.messages[-1].status == "error" and "query" in out.messages[-1].content


# --- pool reducer -----------------------------------------------------------------------------

def test_merge_pool_keeps_the_higher_scored_entry():
    left = {JOB_A: candidate(JOB_A, 0.5).to_dict()}
    right = {JOB_A: candidate(JOB_A, 0.8, search_index=2).to_dict(), JOB_B: candidate(JOB_B).to_dict()}
    merged = merge_pool(left, right)
    assert merged[JOB_A]["score"] == 0.8 and merged[JOB_A]["search_index"] == 2
    assert set(merged) == {JOB_A, JOB_B}
    assert merge_pool(right, {JOB_A: candidate(JOB_A, 0.4, search_index=3).to_dict()})[JOB_A]["search_index"] == 2


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
    assert out.events == [{"status": "Reading 1 job description"}]


async def test_inspect_refuses_more_than_three(run_tool):
    out = await run_tool("inspect_job", {"ids": ["a", "b", "c", "d"]})
    assert "at most 3" in out.messages[-1].content


# --- ask_user ---------------------------------------------------------------------------------

async def test_ask_user_pauses_then_applies_chosen_option(run_tool_with_resume):
    out = await run_tool_with_resume(
        "ask_user", {"question": "Allow hybrid?", "options": [{"label": "Allow hybrid", "relax": ["remote only"]},
                                                              {"label": "Keep remote only"}]},
        state={"brief": Brief(items=[REMOTE]).model_dump(), "turns": 2, **SEARCHED}, answer="Allow hybrid")
    assert out.interrupt_value == {"question": "Allow hybrid?", "options": ["Allow hybrid", "Keep remote only"]}
    assert Brief(**out.state["brief"]).items[0].source == "inferred"
    assert out.state["questions_asked"] == 1
    assert out.messages[-1].content == "User chose: Allow hybrid"
    assert out.events == []  # the question itself is what the user sees
    assert out.state["qa"] == [{"question": "Allow hybrid?", "options": ["Allow hybrid", "Keep remote only"],
                                "answer": "Allow hybrid", "chosen": "Allow hybrid"}]
    assert "unlock_turn" not in out.state


async def test_ask_user_free_text_sets_unlock_turn(run_tool_with_resume):
    out = await run_tool_with_resume("ask_user", {"question": "Q?", "options": [{"label": "A"}]},
                                     state={"turns": 4, **SEARCHED}, answer="hybrid is fine if in Milan")
    assert out.state["unlock_turn"] == 4
    assert out.messages[-1].content == "User answered: hybrid is fine if in Milan"
    assert out.state["qa"][0]["chosen"] is None


async def test_ask_user_past_budget_does_not_interrupt(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": []}, state={"questions_asked": 2})
    assert out.interrupt_value is None and "No more questions" in out.messages[-1].content


async def test_ask_user_without_options_is_refused_without_interrupting(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": []}, state=SEARCHED)
    assert out.interrupt_value is None and "needs options" in out.messages[-1].content


async def test_ask_user_relaxing_an_unknown_item_is_refused_without_interrupting(run_tool):
    out = await run_tool("ask_user", {"question": "Allow hybrid?",
                                      "options": [{"label": "Allow hybrid", "relax": ["remote"]}, {"label": "Keep"}]},
                         state={"brief": Brief(items=[REMOTE]).model_dump(), **SEARCHED})
    assert out.interrupt_value is None
    assert "'remote'" in out.messages[-1].content and "not a stated item" in out.messages[-1].content


async def test_ask_user_dict_resume_value_uses_its_answer(run_tool_with_resume):
    out = await run_tool_with_resume("ask_user", {"question": "Q?", "options": [{"label": "A"}]},
                                     answer={"answer": "A"}, state=SEARCHED)
    assert out.messages[-1].content == "User chose: A"


async def test_ask_user_has_no_side_effects_before_the_answer(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": [{"label": "A"}]}, state={"turns": 1, **SEARCHED})
    assert out.interrupt_value == {"question": "Q?", "options": ["A"]}
    assert out.state.get("questions_asked", 0) == 0 and out.state.get("qa", []) == []
    assert isinstance(out.messages[-1], AIMessage)


async def test_ask_user_before_any_search_is_refused(run_tool):
    out = await run_tool("ask_user", {"question": "Q?", "options": [{"label": "A"}]}, state={"turns": 1})
    assert out.interrupt_value is None and "never ask at the start" in out.messages[-1].content


async def test_ask_user_with_no_turn_left_is_refused(run_tool):
    from backend.search.advanced.budget import MAX_TURNS

    out = await run_tool("ask_user", {"question": "Q?", "options": [{"label": "A"}]},
                         state={"turns": MAX_TURNS, **SEARCHED})
    assert out.interrupt_value is None and "No turns left" in out.messages[-1].content


async def test_ask_user_relaxing_without_a_keep_option_is_refused(run_tool):
    out = await run_tool("ask_user", {"question": "Allow hybrid?",
                                      "options": [{"label": "Allow hybrid", "relax": ["remote only"]}]},
                         state={"brief": Brief(items=[REMOTE]).model_dump(), **SEARCHED})
    assert out.interrupt_value is None and "an option with no relax" in out.messages[-1].content


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


# --- the posting window ---------------------------------------------------------------------------

def _windows(mock) -> list[int]:
    return [c.kwargs["variant"].max_age_hours for c in mock.call_args_list]


def _window_item(text: str, days: int) -> dict:
    return {"text": text, "kind": "require", "source": "stated", "field": "posted_within_days", "value": days}


async def test_a_search_widens_as_far_as_it_asks(run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    out = await run_tool("search_jobs", {"variants": [{"query": "a", "max_age_hours": 336}, {"query": "b"}]},
                         state=WROTE)
    assert _windows(patched_run_variant) == [336, 72]  # no fixed steps; a variant without one: the run's window
    assert out.state["window_hours"] == 336
    assert [s["max_age_hours"] for s in out.state["searches"]] == [336, 72]
    assert "· last 14d" in out.messages[-1].content.split("\n\n")[0].splitlines()[0]  # the header shows it


async def test_a_search_without_a_window_uses_the_widest_the_run_used(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}]}, state={"window_hours": 168, **WROTE})
    assert _windows(patched_run_variant) == [168] and out.state["window_hours"] == 168


async def test_a_narrower_search_keeps_the_widest_window(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "a", "max_age_hours": 24}]},
                         state={"window_hours": 168, **WROTE})
    assert _windows(patched_run_variant) == [24] and out.state["window_hours"] == 168


async def test_searches_default_to_the_stated_window_and_a_wider_one_loosens_it(
        run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    brief = Brief(items=[_window_item("last 3 days", 3)]).model_dump()
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b", "max_age_hours": 336}]},
                         state={"brief": brief, "window_hours": 336, **WROTE})
    assert _windows(patched_run_variant) == [72, 336]  # the stated window, not the widest used
    assert [s["loosened"] for s in out.state["searches"]] == [[], ["posted in the last 3 days → last 14 days"]]


def _remembered(days: int) -> dict:
    return {"text": f"jobs from the last {days} days", "kind": "prefer", "source": "inferred",
            "field": "posted_within_days", "value": days}


async def test_a_remembered_two_weeks_starts_the_run_at_two_weeks(run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    brief = Brief(items=[_remembered(14)]).model_dump()
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}]}, state={"brief": brief, **WROTE})
    assert _windows(patched_run_variant) == [336] and out.state["window_hours"] == 336
    assert "window:" not in out.messages[-1].content


async def test_a_remembered_week_starts_at_7_days_and_the_next_search_may_reach_the_cap(
        run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    brief = Brief(items=[_remembered(7)]).model_dump()
    first = await run_tool("search_jobs", {"variants": [{"query": "a", "max_age_hours": 336}]},
                           state={"brief": brief, **WROTE})
    second = await run_tool("search_jobs", {"variants": [{"query": "a", "max_age_hours": 336}]},
                            state={"brief": brief, "window_hours": first.state["window_hours"], **WROTE})
    assert _windows(patched_run_variant) == [336, 336]
    plain = await run_tool("search_jobs", {"variants": [{"query": "a"}]}, state={"brief": brief, **WROTE})
    assert _windows(patched_run_variant)[-1] == 168 and plain.state["window_hours"] == 168
    assert second.state["window_hours"] == 336


async def test_without_a_remembered_window_the_run_starts_at_3_days(run_tool, patched_run_variant):
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}]}, state=WROTE)
    assert _windows(patched_run_variant) == [72] and out.state["window_hours"] == 72


async def test_a_remembered_window_starts_the_run_at_any_number_of_days(run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    brief = Brief(items=[_remembered(5)]).model_dump()
    out = await run_tool("search_jobs", {"variants": [{"query": "a"}, {"query": "b", "max_age_hours": 336}]},
                         state={"brief": brief, **WROTE})
    assert _windows(patched_run_variant) == [120, 336] and out.state["window_hours"] == 336


async def test_a_remembered_window_written_late_counts_from_the_next_search_and_never_narrows(
        run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    late = await run_tool("search_jobs", {"variants": [{"query": "a"}]},
                          state={"brief": Brief(items=[_remembered(14)]).model_dump(), "window_hours": 72,
                                 "searches_run": 1, **WROTE})
    narrow = await run_tool("search_jobs", {"variants": [{"query": "a"}]},
                            state={"brief": Brief(items=[_remembered(3)]).model_dump(), "window_hours": 168,
                                   "searches_run": 1, **WROTE})
    assert _windows(patched_run_variant) == [336, 168]
    assert late.state["window_hours"] == 336 and narrow.state["window_hours"] == 168


async def test_a_stated_window_overrides_a_remembered_one(run_tool, patched_run_variant, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    brief = Brief(items=[_remembered(14), _window_item("this week", 7)]).model_dump()
    await run_tool("search_jobs", {"variants": [{"query": "a"}]}, state={"brief": brief, **WROTE})
    assert _windows(patched_run_variant) == [168]


def test_a_remembered_window_is_no_hard_filter_and_no_eligibility_rule(pool_state):
    from backend.search.advanced.brief import hard_filters, misses
    from backend.search.advanced.finalize import finalize_candidates
    from tests.search.advanced.conftest import NOW

    brief = Brief(items=[REMOTE, _remembered(1)])
    assert hard_filters(brief).max_age_hours is None and misses(brief, {"posted_within_days": 9.0}) == []
    old = {JOB_A: candidate(JOB_A, 0.9, posted_at="2026-10-25T10:00:00+00:00").to_dict()}  # 9 days before NOW
    assert [c.job_id for c in finalize_candidates({**pool_state, "brief": brief.model_dump(), "pool": old}, NOW)] == [JOB_A]


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
