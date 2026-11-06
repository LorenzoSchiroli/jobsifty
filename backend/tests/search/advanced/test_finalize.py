from backend.search.advanced.budget import FINALIZE_CANDIDATES
from backend.search.advanced.finalize import (
    FinalShortlist, filters_summary, finalize_candidates, partial_result, run_finalize,
)
from tests.search.advanced.conftest import JOB_A, JOB_B, JOB_C, NOW, PROBE_JOB_ID, item
from tests.search.advanced.fakes import ScriptedChatModel
from tests.search.advanced.samples import candidate


async def test_finalize_drops_invented_and_violating_jobs(ctx_factory, pool_state):
    llm = ScriptedChatModel(script=[], structured=[FinalShortlist(summary="s", shortlist=[
        item("a1f3", 90), item("zzzz", 80), item("b2c4", 70)])])   # b2c4 is on-site, brief says remote only
    out = await run_finalize(pool_state, ctx_factory(llm=llm), timeout=30)
    assert [r["job_id"] for r in out["shortlist"]] == [pool_state["short_ids"]["a1f3"]]
    assert out["status"] == "done" and out["summary"] == "s"


async def test_finalize_prompt_carries_cv_rules_and_candidate_text(ctx_factory, pool_state):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[])])
    await run_finalize({**pool_state, "notes": "my notes"}, ctx_factory(llm=llm, cv_text="PyTorch CV"), timeout=30)
    text = "\n".join(str(m.content) for m in llm.calls[0])
    assert "PyTorch CV" in text and "my notes" in text and "- remote only" in text
    assert "[a1f3]" in text and "Build ranking models" in text and 'search 1 "ml"' in text
    assert "[b2c4]" not in text  # violating candidates never reach the model


async def test_finalize_prompt_lists_stated_requirements_and_inferred_preferences_apart(ctx_factory, pool_state):
    from backend.search.advanced.brief import Brief, BriefItem
    from tests.search.advanced.samples import NO_CONSULTING, REMOTE, STARTUP

    senior = BriefItem(text="mid-senior", kind="require", source="inferred", field="seniority", value=["mid", "senior"])
    state = {**pool_state, "brief": Brief(items=[REMOTE, NO_CONSULTING, senior, STARTUP]).model_dump()}
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[])])
    await run_finalize(state, ctx_factory(llm=llm), timeout=30)
    text = str(llm.calls[0][1].content)
    stated = text.split("Stated requirements (said by the user; every result must meet these; the only requirements):\n")[1]
    stated, inferred = stated.split("\n\nInferred preferences (not requirements; you may trade them):\n")
    assert stated == "- remote only (location_type: remote)\n- no consulting (avoid; is_consulting: True)"
    assert inferred.split("\n\n")[0] == "- mid-senior (seniority: mid/senior)\n- startup (prefer; is_startup: True)"


def test_finalize_rules_keep_requirements_for_stated_items_and_forbid_ids():
    from backend.search.advanced.finalize import RULES

    assert "Only the stated requirements are requirements" in RULES
    assert "never call them requirements" in RULES
    assert "inferred requirements" not in RULES  # the rules themselves never call them requirements
    assert "Never mention job ids or short ids" in RULES and "anywhere in the output text" in RULES


def test_candidates_put_keep_first_and_cap_at_twelve(pool_state):
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(1, 21)]
    pool = {j: candidate(j, i / 100).to_dict() for i, j in enumerate(ids, 1)}
    out = finalize_candidates({**pool_state, "pool": pool, "keep": [ids[0]]}, NOW)
    assert len(out) == FINALIZE_CANDIDATES and out[0].job_id == ids[0]
    assert [c.score for c in out[1:]] == sorted((c.score for c in out[1:]), reverse=True)


def test_candidates_skip_violating_jobs(pool_state):
    assert [c.job_id for c in finalize_candidates({**pool_state, "keep": [JOB_C]}, NOW)] == [JOB_A]


def test_candidates_skip_probe_jobs_breaking_locked_items(pool_state_with_probe):
    ids = [c.job_id for c in finalize_candidates(pool_state_with_probe, NOW)]
    assert PROBE_JOB_ID not in ids and JOB_A in ids


def test_candidates_include_probe_jobs_once_relaxed(pool_state_with_probe_relaxed):
    assert PROBE_JOB_ID in [c.job_id for c in finalize_candidates(pool_state_with_probe_relaxed, NOW)]


def test_partial_result_has_no_fit_fields(pool_state):
    out = partial_result(pool_state, NOW)
    assert out["status"] == "partial" and all(r["fit_score"] is None for r in out["shortlist"])


def test_partial_result_skips_jobs_with_breaks(pool_state_with_probe):
    out = partial_result(pool_state_with_probe, NOW)
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]


def test_filters_summary_lists_searches_that_found_shortlisted_jobs(pool_state):
    state = {**pool_state, "shortlist": [{"job_id": JOB_A}]}
    assert filters_summary(state) == "location_type=remote, seniority=senior"
    assert filters_summary({**pool_state, "shortlist": []}) == ""


async def _run(ctx_factory, state, items):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=items)])
    return await run_finalize(state, ctx_factory(llm=llm), timeout=30)


def _many_state(pool_state, n):
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(1, n + 1)]
    return {**pool_state, "keep": ids[:10],
            "pool": {j: candidate(j, 0.5).to_dict() for j in ids},
            "short_ids": {f"{i:04x}": j for i, j in enumerate(ids, 1)}}


async def test_finalize_caps_at_ten_in_model_order_without_failing(ctx_factory, pool_state):
    state = _many_state(pool_state, 12)
    out = await _run(ctx_factory, state, [item(f"{i:04x}", 50) for i in range(1, 12)])
    assert [r["job_id"] for r in out["shortlist"]] == [state["short_ids"][f"{i:04x}"] for i in range(1, 11)]


async def test_finalize_clamps_fit_score(ctx_factory, pool_state):
    out = await _run(ctx_factory, pool_state, [item("a1f3", 140)])
    assert out["shortlist"][0]["fit_score"] == 100


async def test_finalize_normalises_ids_and_drops_duplicates(ctx_factory, pool_state):
    out = await _run(ctx_factory, pool_state, [item(" [A1F3] ", 90), item("a1f3", 80)])
    assert [(r["job_id"], r["fit_score"]) for r in out["shortlist"]] == [(JOB_A, 90)]


def test_partial_result_drops_violating_jobs(pool_state):
    assert [r["job_id"] for r in partial_result(pool_state, NOW)["shortlist"]] == [JOB_A]


def test_partial_result_includes_probe_jobs_once_relaxed(pool_state_with_probe_relaxed):
    ids = [r["job_id"] for r in partial_result(pool_state_with_probe_relaxed, NOW)["shortlist"]]
    assert PROBE_JOB_ID in ids


def test_probe_break_in_other_casing_still_blocks_the_job(pool_state_with_probe):
    pool = dict(pool_state_with_probe["pool"])
    pool[PROBE_JOB_ID] = {**pool[PROBE_JOB_ID], "breaks": ["IN MILAN"]}  # remote: meets the other item
    state = {**pool_state_with_probe, "pool": pool}
    assert PROBE_JOB_ID not in [c.job_id for c in finalize_candidates(state, NOW)]
    assert PROBE_JOB_ID not in [r["job_id"] for r in partial_result(state, NOW)["shortlist"]]


def test_pool_job_with_unknown_field_is_not_eligible_once_the_item_is_stated(pool_state):
    # JOB_B was found before "remote only" was stated: its location type is unknown
    pool = {**pool_state["pool"], JOB_B: candidate(JOB_B, 0.99, location_type=None).to_dict()}
    state = {**pool_state, "pool": pool, "keep": [JOB_B]}
    assert JOB_B not in [c.job_id for c in finalize_candidates(state, NOW)]
    assert JOB_B not in [r["job_id"] for r in partial_result(state, NOW)["shortlist"]]


def _languages_state(pool_state, required: list[str]) -> dict:
    from backend.search.advanced.brief import Brief, BriefItem
    from tests.search.advanced.samples import REMOTE

    speaks = BriefItem(text="speaks Italian, English, German", kind="require", source="inferred",
                       field="languages_required", value=["italian", "english", "german"])
    return {**pool_state, "brief": Brief(items=[REMOTE, speaks]).model_dump(),
            "pool": {JOB_A: candidate(JOB_A, 0.91, languages=required).to_dict()}}


async def test_finalize_adds_a_gap_for_a_language_the_user_does_not_list(ctx_factory, pool_state):
    state = _languages_state(pool_state, ["spanish"])
    model_item = item("a1f3", 90).model_copy(update={"gaps": ["Requires Spanish", "no Kubernetes"]})
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[model_item])])
    out = await run_finalize(state, ctx_factory(llm=llm), timeout=30)
    assert out["shortlist"][0]["gaps"] == ["requires Spanish", "no Kubernetes"]  # merged without the repeat
    text = "\n".join(str(m.content) for m in llm.calls[0])
    assert "known gaps: requires Spanish" in text


async def test_finalize_adds_no_gap_for_english_or_unknown_languages(ctx_factory, pool_state):
    for required in (["english"], []):
        out = await _run(ctx_factory, _languages_state(pool_state, required), [item("a1f3", 90)])
        assert out["shortlist"][0]["gaps"] == []


def test_partial_result_carries_the_code_gaps(pool_state):
    out = partial_result(_languages_state(pool_state, ["english", "spanish"]), NOW)
    assert out["shortlist"][0]["gaps"] == ["requires Spanish"]


def _seniority_state(pool_state) -> dict:
    from backend.search.advanced.brief import Brief, BriefItem
    from tests.search.advanced.samples import REMOTE

    mid = BriefItem(text="mid level", kind="require", source="inferred", field="seniority", value="mid")
    return {**pool_state, "brief": Brief(items=[REMOTE, mid]).model_dump(),
            "pool": {JOB_A: candidate(JOB_A, 0.91, seniority="lead").to_dict()}}


async def test_other_inferred_contradictions_become_trade_offs_not_gaps(ctx_factory, pool_state):
    model_item = item("a1f3", 70).model_copy(update={"trade_offs": ["LEAD LEVEL, NOT MID", "long commute"]})
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[model_item])])
    out = await run_finalize(_seniority_state(pool_state), ctx_factory(llm=llm), timeout=30)
    assert out["shortlist"][0]["trade_offs"] == ["lead level, not mid", "long commute"]
    assert out["shortlist"][0]["gaps"] == []
    assert "known trade-offs: lead level, not mid" in str(llm.calls[0][1].content)
    partial = partial_result(_seniority_state(pool_state), NOW)["shortlist"][0]
    assert (partial["gaps"], partial["trade_offs"]) == ([], ["lead level, not mid"])


def test_a_stated_window_drops_older_pool_jobs(pool_state):
    from backend.search.advanced.brief import Brief, BriefItem
    from tests.search.advanced.samples import REMOTE

    def brief(days):
        window = BriefItem(text="recent", kind="require", source="stated", field="posted_within_days", value=days)
        return Brief(items=[REMOTE, window]).model_dump()

    # JOB_A was posted 26 hours before NOW: within 2 days, not within 1
    assert [c.job_id for c in finalize_candidates({**pool_state, "brief": brief(2)}, NOW)] == [JOB_A]
    assert finalize_candidates({**pool_state, "brief": brief(1)}, NOW) == []
    assert partial_result({**pool_state, "brief": brief(1)}, NOW)["shortlist"] == []


async def test_finalize_drops_a_returned_job_that_does_not_meet_a_stated_item(ctx_factory, pool_state):
    pool = {**pool_state["pool"], JOB_B: candidate(JOB_B, 0.5, location_type=None).to_dict()}
    state = {**pool_state, "pool": pool, "short_ids": {**pool_state["short_ids"], "9c02": JOB_B}}
    out = await _run(ctx_factory, state, [item("9c02", 90), item("a1f3", 80)])
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]


async def test_finalize_prompt_carries_the_questions_and_answers(ctx_factory, pool_state):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[])])
    qa = [{"question": "Allow hybrid?", "options": ["Yes", "No"], "answer": "only if in Milan", "chosen": None}]
    await run_finalize({**pool_state, "qa": qa}, ctx_factory(llm=llm), timeout=30)
    text = "\n".join(str(m.content) for m in llm.calls[0])
    assert "- Q: Allow hybrid? A: only if in Milan" in text


async def test_finalize_caps_output_tokens(ctx_factory, pool_state):
    from backend.search.advanced.budget import FINALIZE_MAX_TOKENS

    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[])])
    await run_finalize(pool_state, ctx_factory(llm=llm), timeout=30)
    assert llm.structured_kwargs["max_tokens"] == FINALIZE_MAX_TOKENS
