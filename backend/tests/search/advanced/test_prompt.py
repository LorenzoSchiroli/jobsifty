from langchain_core.messages import HumanMessage, ToolMessage

from backend.search.advanced.brief import Brief
from backend.search.advanced.prompt import compact_history, system_prompt
from tests.search.advanced.conftest import JOB_A
from tests.search.advanced.samples import REMOTE, candidate


def search_msg(content: str, call_id: str) -> ToolMessage:
    return ToolMessage(content, tool_call_id=call_id, name="search_jobs")


def test_compaction_keeps_only_latest_search_in_full():
    msgs = [search_msg('search 1 "a" · locked: —\n  lines…', "c1"), search_msg('search 2 "b" · locked: —\n  lines…', "c2")]
    out = compact_history(msgs)
    assert out[0].content == 'search 1 "a" · locked: —' and out[0].tool_call_id == "c1"
    assert out[1].content == msgs[1].content
    assert "lines" in msgs[0].content  # the input is not mutated


def test_compaction_keeps_every_variant_header_and_other_messages():
    multi = search_msg('search 1 "a"\n  x\npool: 1\n\nsearch 2 "b"\n  y\npool: 2', "c1")
    other = ToolMessage("big result", tool_call_id="c0", name="inspect_job")
    msgs = [HumanMessage("hi"), other, multi, search_msg('search 3 "c"\n z', "c3")]
    out = compact_history(msgs)
    assert out[2].content == 'search 1 "a"\nsearch 2 "b"'
    assert out[0] is msgs[0] and out[1].content == "big result"


def test_system_prompt_cuts_cv_and_shows_locked_items(ctx_factory):
    prompt = system_prompt({"brief": Brief(items=[REMOTE]).model_dump(), "turns": 1}, ctx_factory(cv_text="x" * 5000))
    assert "x" * 3000 in prompt and "x" * 3001 not in prompt
    assert "remote only" in prompt and "stated" in prompt


def test_system_prompt_carries_rules_budget_and_pool_digest(ctx_factory):
    state = {"turns": 2, "searches_run": 1, "pool": {JOB_A: candidate(JOB_A, 0.91).to_dict()},
             "short_ids": {"a1f3": JOB_A}}
    prompt = system_prompt(state, ctx_factory(preference_memory="likes startups"))
    assert "One tool call per turn" in prompt and "up to 3 variants" in prompt
    assert "turns 4, searches 8, questions 2" in prompt
    assert "pool: 1 jobs, best: [a1f3] ML Engineer (0.91)" in prompt
    assert "likes startups" in prompt


def test_system_prompt_asks_for_the_cv_languages_as_an_inferred_item(ctx_factory):
    prompt = system_prompt({"turns": 0}, ctx_factory())
    assert "record the languages the user can work in" in prompt
    assert "inferred require item on languages_required" in prompt


def test_the_starting_window_is_never_wider_than_the_live_window(ctx_factory, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "2")
    prompt = system_prompt({"turns": 0}, ctx_factory())
    assert "starting at 48 (2 days)" in prompt and "instead of 2 days" in prompt
    assert "72 (3 days)" not in prompt


def test_system_prompt_leaves_widening_to_the_agent_up_to_the_live_window(ctx_factory, monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "10")
    prompt = system_prompt({"turns": 0}, ctx_factory())
    assert "too few or unsatisfying, as far as you judge" in prompt and "one step per search" not in prompt
    assert "nothing older than 240 hours (10 days)" in prompt
    assert "that the preference memory or the CV states (only then: never invent one)" in prompt
    assert "inferred posted_within_days item" in prompt and "The run then starts at that window" in prompt
    assert "never call it a requirement" in prompt and "it wins over a remembered one" in prompt
    assert "A wider max_age_hours loosens it" in prompt
    assert "finish notes say so" in prompt


def test_system_prompt_explains_the_ten_results_and_loosening(ctx_factory):
    prompt = system_prompt({"turns": 0}, ctx_factory())
    assert "A ranked list of 10 jobs" in prompt and "fill it up" in prompt
    assert "loosen stated items (`loosen`)" in prompt and "then Austria and Switzerland" in prompt and "never regions" in prompt
    assert "never matches" in prompt and "ask_user instead" in prompt
    assert "probe" not in prompt
