from backend.search.advanced.budget import FINALIZE_CANDIDATES
from backend.search.advanced.finalize import (
    FinalShortlist, filters_summary, finalize_candidates, partial_result, run_finalize,
)
from tests.search.advanced.conftest import JOB_A, JOB_C, PROBE_JOB_ID, item
from tests.search.advanced.fakes import ScriptedChatModel
from tests.search.advanced.samples import candidate


async def test_finalize_drops_invented_and_violating_jobs(ctx_factory, pool_state):
    llm = ScriptedChatModel(script=[], structured=[FinalShortlist(summary="s", shortlist=[
        item("a1f3", 90), item("zzzz", 80), item("b2c4", 70)])])   # b2c4 is on-site, brief says remote only
    out = await run_finalize(pool_state, ctx_factory(llm=llm))
    assert [r["job_id"] for r in out["shortlist"]] == [pool_state["short_ids"]["a1f3"]]
    assert out["status"] == "done" and out["summary"] == "s"


async def test_finalize_prompt_carries_cv_rules_and_candidate_text(ctx_factory, pool_state):
    llm = ScriptedChatModel(structured=[FinalShortlist(summary="s", shortlist=[])])
    await run_finalize({**pool_state, "notes": "my notes"}, ctx_factory(llm=llm, cv_text="PyTorch CV"))
    text = "\n".join(str(m.content) for m in llm.calls[0])
    assert "PyTorch CV" in text and "my notes" in text and "- remote only" in text
    assert "[a1f3]" in text and "Build ranking models" in text and 'search 1 "ml"' in text
    assert "[b2c4]" not in text  # violating candidates never reach the model


def test_candidates_put_keep_first_and_cap_at_twelve(pool_state):
    ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(1, 21)]
    pool = {j: candidate(j, i / 100).to_dict() for i, j in enumerate(ids, 1)}
    out = finalize_candidates({**pool_state, "pool": pool, "keep": [ids[0]]})
    assert len(out) == FINALIZE_CANDIDATES and out[0].job_id == ids[0]
    assert [c.score for c in out[1:]] == sorted((c.score for c in out[1:]), reverse=True)


def test_candidates_skip_violating_jobs(pool_state):
    assert [c.job_id for c in finalize_candidates({**pool_state, "keep": [JOB_C]})] == [JOB_A]


def test_candidates_skip_probe_jobs_breaking_locked_items(pool_state_with_probe):
    ids = [c.job_id for c in finalize_candidates(pool_state_with_probe)]
    assert PROBE_JOB_ID not in ids and JOB_A in ids


def test_candidates_include_probe_jobs_once_relaxed(pool_state_with_probe_relaxed):
    assert PROBE_JOB_ID in [c.job_id for c in finalize_candidates(pool_state_with_probe_relaxed)]


def test_partial_result_has_no_fit_fields(pool_state):
    out = partial_result(pool_state)
    assert out["status"] == "partial" and all(r["fit_score"] is None for r in out["shortlist"])


def test_partial_result_skips_jobs_with_breaks(pool_state_with_probe):
    out = partial_result(pool_state_with_probe)
    assert [r["job_id"] for r in out["shortlist"]] == [JOB_A]


def test_filters_summary_lists_searches_that_found_shortlisted_jobs(pool_state):
    state = {**pool_state, "shortlist": [{"job_id": JOB_A}]}
    assert filters_summary(state) == "location_type=remote, seniority=senior"
    assert filters_summary({**pool_state, "shortlist": []}) == ""
