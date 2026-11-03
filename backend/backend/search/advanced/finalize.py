"""Finalize: the pool becomes the shortlist with one structured LLM call."""
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.search.advanced.brief import Brief, violations
from backend.search.advanced.budget import CV_CHARS, FINALIZE_CANDIDATES, FINALIZE_TEXT_CHARS, MAX_SHORTLIST
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import Candidate, job_line
from backend.search.advanced.state import AgentState

PARTIAL_SUMMARY = "The assistant could not finish; these are the most relevant jobs found so far."

RULES = """You write the final shortlist of jobs for one user from the candidates below.

- Return up to 10 jobs, best fit first. Fewer is fine; never pad the list with jobs that break what the user asked for.
- job_id is the id in square brackets. fit_score is 0-100. fits: what aligns, one line. trade_offs: what the user gives up, short phrases (may be empty); name only inferred preferences, never a stated requirement. gaps: requirements the user does not meet, short phrases (may be empty).
- The stated requirements are rules every job in the shortlist must meet.
- Judge by the job text, not the title alone.
- summary: two or three sentences on what was tried and concluded, honest about trade-offs and about how few good jobs there were, if so."""


class ShortlistItem(BaseModel):
    job_id: str = Field(description="the short id in square brackets")
    fit_score: int = Field(ge=0, le=100)
    fits: str
    trade_offs: list[str]
    gaps: list[str]


class FinalShortlist(BaseModel):
    shortlist: list[ShortlistItem] = Field(max_length=MAX_SHORTLIST)
    summary: str


def _brief(state: AgentState) -> Brief:
    return Brief.model_validate(state.get("brief") or {})


def _eligible(c: Candidate, brief: Brief) -> bool:
    stated = {i.text for i in brief.locked()}
    if any(b in stated for b in c.breaks):
        return False
    return not violations(brief, c.fields())


def finalize_candidates(state: AgentState) -> list[Candidate]:
    brief = _brief(state)
    pool = state.get("pool") or {}
    keep = [j for j in (state.get("keep") or []) if j in pool][:MAX_SHORTLIST]
    rest = sorted((j for j in pool if j not in keep), key=lambda j: -pool[j]["score"])
    out: list[Candidate] = []
    for job_id in [*keep, *rest]:
        c = Candidate.from_dict(pool[job_id])
        if _eligible(c, brief):
            out.append(c)
        if len(out) == FINALIZE_CANDIDATES:
            break
    return out


def finalize_prompt(state: AgentState, ctx: AgentContext, candidates: list[Candidate]) -> list[BaseMessage]:
    brief = _brief(state)
    by_job = {job_id: short for short, job_id in (state.get("short_ids") or {}).items()}
    rules = [i.text for i in brief.locked()]
    blocks = [
        f"CV:\n{ctx.cv_text[:CV_CHARS]}",
        f"Preference memory (inferred):\n{ctx.preference_memory or '—'}",
        f"Brief:\n{brief.to_api()}",
        f"Agent notes:\n{state.get('notes') or '—'}",
        "Stated requirements (every result must meet these):\n" + ("\n".join(f"- {r}" for r in rules) or "—"),
        "Candidates:\n" + "\n".join(
            f"{job_line(c, by_job.get(c.job_id, c.job_id[:4]), ctx.now)}\n    {c.text[:FINALIZE_TEXT_CHARS]}"
            for c in candidates),
        "Searches:\n" + ("\n".join(s["header"] for s in state.get("searches") or []) or "—"),
    ]
    return [SystemMessage(RULES), HumanMessage("\n\n".join(blocks))]


async def run_finalize(state: AgentState, ctx: AgentContext) -> dict:
    brief = _brief(state)
    candidates = finalize_candidates(state)
    by_job = {c.job_id: c for c in candidates}
    short_ids = state.get("short_ids") or {}
    structured = ctx.llm.with_structured_output(FinalShortlist, method="json_schema", strict=True)
    result: FinalShortlist = await structured.ainvoke(finalize_prompt(state, ctx, candidates))
    shortlist = []
    for item in result.shortlist:
        job_id = short_ids.get(item.job_id)
        c = by_job.get(job_id) if job_id else None
        if c is None or violations(brief, c.fields()):
            continue
        shortlist.append({**item.model_dump(), "job_id": job_id})
        if len(shortlist) == MAX_SHORTLIST:
            break
    return {"status": "done", "shortlist": shortlist, "summary": result.summary}


def partial_result(state: AgentState) -> dict:
    pool = state.get("pool") or {}
    ranked = sorted((c for c in pool.values() if not c.get("breaks")), key=lambda c: -c["score"])
    return {
        "status": "partial",
        "shortlist": [{"job_id": c["job_id"], "fit_score": None, "fits": "", "trade_offs": [], "gaps": []}
                      for c in ranked[:MAX_SHORTLIST]],
        "summary": PARTIAL_SUMMARY,
    }


def filters_summary(state: AgentState) -> str:
    """Filters (locked and soft) of the searches that produced a shortlisted job, for the preference memory."""
    pool = state.get("pool") or {}
    used = {pool[r["job_id"]]["search_index"] for r in state.get("shortlist") or [] if r["job_id"] in pool}
    parts = []
    for s in state.get("searches") or []:
        if s["index"] not in used:
            continue
        filters = dict(s["filters"])
        locked = filters.pop("locked", None) or {}
        items = [f"{k}={'/'.join(v) if isinstance(v, list) else v}" for k, v in {**locked, **filters}.items()
                 if v is not None]
        if items:
            parts.append(", ".join(items))
    return "; ".join(parts)
