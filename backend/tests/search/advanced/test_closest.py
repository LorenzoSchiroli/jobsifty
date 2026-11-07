"""The closest jobs: shown apart from the matches, with what each misses, filling the result up to 10."""
from backend.search.advanced.brief import Brief, BriefItem
from backend.search.advanced.finalize import (
    FinalShortlist, closest_candidates, gather_closest, partial_result, run_finalize,
)
from tests.search.advanced.conftest import JOB_A, JOB_B, JOB_C, NOW, LOOSE_JOB_ID, item
from tests.search.advanced.fakes import ScriptedChatModel
from tests.search.advanced.samples import NO_CONSULTING, REMOTE, candidate


async def _run(ctx_factory, state, items):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", jobs=items)])
    return await run_finalize(state, ctx_factory(llm=llm), timeout=30)


def _nlp_state(pool_state) -> dict:
    """Stated: remote only (code checks it) and an NLP role (R1: the model judges it). JOB_B is remote."""
    nlp = BriefItem(text="NLP or search engineer role", kind="require", source="stated")
    pool = {**pool_state["pool"], JOB_B: candidate(JOB_B, 0.8).to_dict()}
    return {**pool_state, "brief": Brief(items=[REMOTE, nlp]).model_dump(), "pool": pool,
            "short_ids": {**pool_state["short_ids"], "9c02": JOB_B}}


def _matching_state(pool_state, n: int) -> dict:
    """n remote (matching) jobs, short ids 0001, 0002, ..., plus JOB_C, on-site."""
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(1, n + 1)]
    pool = {j: candidate(j, 0.5).to_dict() for j in ids} | {JOB_C: candidate(JOB_C, 0.95, location_type="on-site").to_dict()}
    return {**pool_state, "pool": pool, "short_ids": {f"{i:04x}": j for i, j in enumerate(ids, 1)} | {"b2c4": JOB_C}}


async def test_a_job_the_model_marks_unmet_is_among_the_closest_with_what_it_misses(ctx_factory, pool_state):
    out = await _run(ctx_factory, _nlp_state(pool_state), [
        item("a1f3", 85), item("9c02", 55).model_copy(update={"unmet": ["R1"]})])
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]
    assert [(r["job_id"], r["misses"], r["fit_score"]) for r in out["closest"]] == [
        (JOB_B, ["NLP or search engineer role"], 55),
        (JOB_C, ["remote", "NLP or search engineer role (not confirmed)"], None)]  # left out: never judged


async def test_requirement_labels_never_reach_the_text(ctx_factory, pool_state):
    llm = ScriptedChatModel(structured=[FinalShortlist(
        summary="No job meets the NLP focus (R1). R1 rules them out; R7 is no label.",
        jobs=[item("9c02", 55).model_copy(update={"unmet": ["R1"], "fits": "ML, misses R1",
                                                       "gaps": ["no NLP (R1)"]})])])
    out = await run_finalize(_nlp_state(pool_state), ctx_factory(llm=llm), timeout=30)
    assert out["summary"] == 'No job meets the NLP focus. "NLP or search engineer role" rules them out; R7 is no label.'
    assert (out["closest"][0]["fits"], out["closest"][0]["gaps"]) == (
        'ML, misses "NLP or search engineer role"', ["no NLP"])


async def test_a_job_contradicting_a_stated_field_is_among_the_closest(ctx_factory, pool_state):
    out = await _run(ctx_factory, pool_state, [item("a1f3", 90), item("b2c4", 70)])  # b2c4 is on-site
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]
    assert [(r["job_id"], r["misses"]) for r in out["closest"]] == [(JOB_C, ["remote"])]


async def test_no_closest_jobs_once_ten_match(ctx_factory, pool_state):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", jobs=[
        *(item(f"{i:04x}", 80) for i in range(1, 11)), item("b2c4", 70)])])
    out = await run_finalize(_matching_state(pool_state, 10), ctx_factory(llm=llm), timeout=30)
    assert len(out["shortlist"]) == 10 and out["closest"] == []
    assert "Closest candidates" not in str(llm.calls[0][1].content)  # ten eligible: no near-miss is shown


async def test_closest_jobs_fill_the_result_up_to_ten(ctx_factory, pool_state):
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(1, 14)]
    pool = ({j: candidate(j, 0.5).to_dict() for j in ids[:7]}  # seven remote: matches
            | {j: candidate(j, 0.6, location_type="hybrid").to_dict() for j in ids[7:]})  # six hybrid
    state = {**pool_state, "pool": pool, "short_ids": {f"{i:04x}": j for i, j in enumerate(ids, 1)}}
    out = await _run(ctx_factory, state, [item(f"{i:04x}", 90 - i) for i in range(1, 14)])
    assert len(out["shortlist"]) == 7
    assert [r["job_id"] for r in out["closest"]] == ids[7:10]  # three fill it up, best fit first


async def test_closest_jobs_come_fewest_misses_first(ctx_factory, pool_state):
    pool = {JOB_A: candidate(JOB_A, 0.91).to_dict(),
            JOB_B: candidate(JOB_B, 0.99, location_type="on-site", is_consulting=True).to_dict(),  # misses two
            JOB_C: candidate(JOB_C, 0.50, location_type=None).to_dict()}  # misses one: unknown
    state = {**pool_state, "brief": Brief(items=[REMOTE, NO_CONSULTING]).model_dump(), "pool": pool,
             "short_ids": {"a1f3": JOB_A, "9c02": JOB_B, "b2c4": JOB_C}}
    out = await _run(ctx_factory, state, [item("9c02", 90), item("b2c4", 40), item("a1f3", 80)])
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]
    assert [(r["job_id"], r["misses"]) for r in out["closest"]] == [
        (JOB_C, ["remote (unknown)"]), (JOB_B, ["remote", "no consulting"])]


async def test_a_model_keeping_nothing_still_shows_the_closest_unscored(ctx_factory, pool_state):
    out = await _run(ctx_factory, pool_state, [])
    assert [(r["job_id"], r["fit_score"]) for r in out["shortlist"]] == [(JOB_A, None)]  # no label: code checked it all
    assert [(r["job_id"], r["fit_score"], r["misses"]) for r in out["closest"]] == [(JOB_C, None, ["remote"])]


async def test_eligible_jobs_the_model_never_confirmed_show_their_labels_as_not_confirmed(ctx_factory, pool_state):
    state = {**_nlp_state(pool_state), "pool": {JOB_A: candidate(JOB_A, 0.9).to_dict()}}
    out = await _run(ctx_factory, state, [])
    assert out["shortlist"] == []
    assert [(r["job_id"], r["misses"]) for r in out["closest"]] == [(JOB_A, ["NLP or search engineer role (not confirmed)"])]


async def test_a_model_keeping_nothing_shows_the_jobs_with_fewest_misses_first(ctx_factory, pool_state):
    # JOB_A misses only the unconfirmed R1; JOB_C (on-site) misses remote and R1
    out = await _run(ctx_factory, _nlp_state(pool_state) | {"pool": {
        JOB_A: candidate(JOB_A, 0.5).to_dict(), JOB_C: candidate(JOB_C, 0.95, location_type="on-site").to_dict()}}, [])
    assert [(r["job_id"], r["misses"]) for r in out["closest"]] == [
        (JOB_A, ["NLP or search engineer role (not confirmed)"]),
        (JOB_C, ["remote", "NLP or search engineer role (not confirmed)"])]


async def test_without_labels_a_model_keeping_nothing_leaves_the_eligible_jobs_as_matches(ctx_factory, pool_state):
    state = {**pool_state, "pool": {JOB_A: candidate(JOB_A, 0.9).to_dict()}}
    out = await _run(ctx_factory, state, [])
    assert [(r["job_id"], r["fit_score"]) for r in out["shortlist"]] == [(JOB_A, None)] and out["closest"] == []


def test_partial_result_fills_up_with_the_closest_jobs(pool_state):
    out = partial_result(pool_state, NOW)
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]
    assert [(r["job_id"], r["fit_score"], r["misses"]) for r in out["closest"]] == [(JOB_C, None, ["remote"])]
    assert partial_result(_matching_state(pool_state, 10), NOW)["closest"] == []


def test_a_loosened_job_misses_the_stated_item_it_does_not_meet(pool_state_loosened):
    assert [c.job_id for c in closest_candidates(pool_state_loosened, NOW, 10)] == [LOOSE_JOB_ID]
    assert partial_result(pool_state_loosened, NOW)["closest"][0]["misses"] == ["in Milan"]


async def test_gather_closest_searches_when_too_few_jobs_match(ctx_factory, pool_state, patched_closest_search):
    patched_closest_search.return_value = [candidate(JOB_B, 0.7, location_type="hybrid", search_index=0)]
    events = []
    state = {**pool_state, "query": "remote ml please", "window_hours": 168,
             "searches": [{"index": 1, "loosened": ["without remote"], "query": "loose q", "max_age_hours": 72},
                          {"index": 2, "loosened": [], "query": "ml engineer", "max_age_hours": 168}]}
    view, update = await gather_closest(state, ctx_factory(), events.append)
    kw = patched_closest_search.call_args.kwargs
    assert (kw["query"], kw["max_age_hours"]) == ("ml engineer", 168)  # the first unloosened search, the run's window
    assert set(update["pool"]) == {JOB_B} and JOB_B in update["short_ids"].values()
    assert set(view["pool"]) == {JOB_A, JOB_B, JOB_C} and events == [{"status": "Looking for the closest jobs"}]


async def test_gather_closest_keeps_a_stated_window(ctx_factory, pool_state, patched_closest_search):
    week = BriefItem(text="last week", kind="require", source="stated", field="posted_within_days", value=7)
    state = {**pool_state, "brief": Brief(items=[REMOTE, week]).model_dump(), "query": "ml", "searches": []}
    await gather_closest(state, ctx_factory(), lambda _: None)
    assert patched_closest_search.call_args.kwargs["max_age_hours"] == 168


async def test_gather_closest_skips_the_search_when_enough_jobs_match(ctx_factory, pool_state, patched_closest_search):
    state = _matching_state(pool_state, 10)
    assert await gather_closest(state, ctx_factory(), lambda _: None) == (state, {})
    patched_closest_search.assert_not_called()


async def test_gather_closest_search_failure_leaves_the_state(ctx_factory, pool_state, patched_closest_search):
    patched_closest_search.side_effect = RuntimeError("opensearch down")
    assert await gather_closest(pool_state, ctx_factory(), lambda _: None) == (pool_state, {})
