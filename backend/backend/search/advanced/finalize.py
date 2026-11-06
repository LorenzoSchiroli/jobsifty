"""Finalize: the pool becomes the shortlist with one structured LLM call."""
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.search.advanced.brief import Brief, BriefItem, inferred_shortfalls, unmet
from backend.search.advanced.budget import (
    CV_CHARS,
    FINALIZE_CANDIDATES,
    FINALIZE_MAX_TOKENS,
    FINALIZE_TEXT_CHARS,
    MAX_SHORTLIST,
)
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import Candidate, job_line
from backend.search.advanced.state import AgentState

RULES = """You write the final shortlist of jobs for one user from the candidates below.

- Return up to 10 jobs, best fit first. Fewer is fine; never pad the list with jobs that break what the user asked for.
- job_id is the id in square brackets. fit_score is 0-100. fits: what aligns, one line. trade_offs: what the user gives up, short phrases (may be empty); name only inferred preferences, never a stated requirement. gaps: requirements the user does not meet, short phrases (may be empty).
- The stated requirements are rules every job in the shortlist must meet.
- Only the stated requirements are requirements. The inferred preferences (read from the CV, the preference memory or the request) are preferences: in the summary and everywhere else, never call them requirements, and say that jobs meet "the requirements" only of the stated ones.
- Never mention job ids or short ids (the codes in square brackets) anywhere in the output text: summary, fits, trade_offs and gaps name a job by its title and company. Only job_id carries the id.
- A candidate's "known gaps" and "known trade-offs" lines list what its fields contradict among the inferred preferences: a language the CV does not list is a gap, another work mode, seniority or company type is a trade-off. Code adds them to the job's gaps and trade_offs: do not repeat them, but weigh them in fit_score and never explain them away.
- Judge by the job text, not the title alone.
- summary: two or three sentences on what was tried and concluded, honest about trade-offs and about how few good jobs there were, if so."""


class ShortlistItem(BaseModel):
    job_id: str = Field(description="the short id in square brackets")
    fit_score: int = Field(description="0 to 100")
    fits: str
    trade_offs: list[str]
    gaps: list[str]


class FinalShortlist(BaseModel):
    shortlist: list[ShortlistItem]  # up to MAX_SHORTLIST; enforced in run_finalize, not in the schema
    summary: str


def _brief(state: AgentState) -> Brief:
    return Brief.model_validate(state.get("brief") or {})


def _eligible(c: Candidate, brief: Brief, now: datetime) -> bool:
    """A job may be shortlisted (or returned as partial) only if it positively meets every stated
    hard item whose field it carries (a stated window too, at `now`), and breaks no still-stated
    item (probe jobs)."""
    stated = {i.text.casefold() for i in brief.locked()}
    if any(b.casefold() in stated for b in c.breaks):
        return False
    return not unmet(brief, c.fields(now))


def finalize_candidates(state: AgentState, now: datetime) -> list[Candidate]:
    brief = _brief(state)
    pool = state.get("pool") or {}
    keep = [j for j in (state.get("keep") or []) if j in pool][:MAX_SHORTLIST]
    rest = sorted((j for j in pool if j not in keep), key=lambda j: -pool[j]["score"])
    out: list[Candidate] = []
    for job_id in [*keep, *rest]:
        c = Candidate.from_dict(pool[job_id])
        if _eligible(c, brief, now):
            out.append(c)
        if len(out) == FINALIZE_CANDIDATES:
            break
    return out


def _item_lines(items: list[BriefItem]) -> str:
    """One line per brief item: its text, its kind when it is not require, and its field and value."""
    lines = []
    for i in items:
        details = [] if i.kind == "require" else [i.kind]
        if i.field is not None:
            details.append(f"{i.field}: {'/'.join(map(str, i.value)) if isinstance(i.value, list) else i.value}")
        lines.append(f"- {i.text}" + (f" ({'; '.join(details)})" if details else ""))
    return "\n".join(lines) or "—"


def _candidate_block(c: Candidate, short_id: str, brief: Brief, now: datetime) -> str:
    block = f"{job_line(c, short_id, now)}\n    {c.text[:FINALIZE_TEXT_CHARS]}"
    gaps, trade_offs = inferred_shortfalls(brief, c.fields(now))
    if gaps:
        block += "\n    known gaps: " + "; ".join(gaps)
    if trade_offs:
        block += "\n    known trade-offs: " + "; ".join(trade_offs)
    return block


def _merge(code: list[str], model: list[str]) -> list[str]:
    """The code's phrases, then the model's, without repeats (case-insensitive) or blanks."""
    merged: dict[str, str] = {}
    for phrase in [*code, *model]:
        if phrase.strip():
            merged.setdefault(phrase.strip().casefold(), phrase.strip())
    return list(merged.values())


def finalize_prompt(state: AgentState, ctx: AgentContext, candidates: list[Candidate]) -> list[BaseMessage]:
    brief = _brief(state)
    by_job = {job_id: short for short, job_id in (state.get("short_ids") or {}).items()}
    stated = [i for i in brief.items if i.source == "stated"]
    inferred = [i for i in brief.items if i.source == "inferred"]
    blocks = [
        f"CV:\n{ctx.cv_text[:CV_CHARS]}",
        f"Preference memory (inferred):\n{ctx.preference_memory or '—'}",
        "Stated requirements (said by the user; every result must meet these; the only requirements):\n"
        + _item_lines(stated),
        "Inferred preferences (not requirements; you may trade them):\n" + _item_lines(inferred),
        f"Brief notes:\n{brief.notes or '—'}",
        f"Agent notes:\n{state.get('notes') or '—'}",
        "Candidates:\n" + "\n".join(_candidate_block(c, by_job.get(c.job_id, c.job_id[:4]), brief, ctx.now)
                                      for c in candidates),
        "Searches:\n" + ("\n".join(s["header"] for s in state.get("searches") or []) or "—"),
        # A free-text answer the agent did not write into the brief still counts.
        "Questions and the user's answers:\n" + (
            "\n".join(f"- Q: {q['question']} A: {q['answer']}" for q in state.get("qa") or []) or "—"),
    ]
    return [SystemMessage(RULES), HumanMessage("\n\n".join(blocks))]


async def run_finalize(state: AgentState, ctx: AgentContext, timeout: float) -> dict:
    brief = _brief(state)
    candidates = finalize_candidates(state, ctx.now)
    by_job = {c.job_id: c for c in candidates}
    short_ids = state.get("short_ids") or {}
    result: FinalShortlist = await ctx.llm.structured(finalize_prompt(state, ctx, candidates), FinalShortlist,
                                                      FINALIZE_MAX_TOKENS, timeout=timeout)
    shortlist = []
    for item in result.shortlist:
        job_id = short_ids.get(item.job_id.strip().strip("[]").strip().lower())
        c = by_job.get(job_id) if job_id else None
        if c is None or not _eligible(c, brief, ctx.now) or any(r["job_id"] == job_id for r in shortlist):
            continue
        gaps, trade_offs = inferred_shortfalls(brief, c.fields(ctx.now))
        shortlist.append({**item.model_dump(), "job_id": job_id, "fit_score": max(0, min(100, item.fit_score)),
                          "gaps": _merge(gaps, item.gaps), "trade_offs": _merge(trade_offs, item.trade_offs)})
        if len(shortlist) == MAX_SHORTLIST:
            break
    return {"status": "done", "shortlist": shortlist, "summary": result.summary}


def partial_result(state: AgentState, now: datetime) -> dict:
    """The most relevant eligible jobs, without fit fields; gaps and trade-offs are the code's own.
    The summary is empty: no LLM wrote one, and `status: partial` already says the run did not finish."""
    brief = _brief(state)
    pool = (Candidate.from_dict(c) for c in (state.get("pool") or {}).values())
    ranked = sorted((c for c in pool if _eligible(c, brief, now)), key=lambda c: -c.score)
    shortlist = []
    for c in ranked[:MAX_SHORTLIST]:
        gaps, trade_offs = inferred_shortfalls(brief, c.fields(now))
        shortlist.append({"job_id": c.job_id, "fit_score": None, "fits": "", "trade_offs": trade_offs, "gaps": gaps})
    return {
        "status": "partial",
        "shortlist": shortlist,
        "summary": "",
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
