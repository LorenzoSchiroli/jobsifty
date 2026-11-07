"""Finalize: the pool becomes the shortlist with one structured LLM call."""
import logging
import re
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.search.advanced.brief import (
    Brief,
    BriefItem,
    filter_label,
    inferred_shortfalls,
    misses,
    missing_languages,
)
from backend.search.advanced.budget import (
    FINALIZE_CANDIDATES,
    FINALIZE_MAX_TOKENS,
    FINALIZE_TEXT_CHARS,
    MAX_SHORTLIST,
)
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import Candidate, assign_short_ids, job_line, window_text
from backend.search.advanced.search import closest_search
from backend.search.advanced.state import AgentState, merge_pool
from backend.search.advanced.tools import run_window

logger = logging.getLogger(__name__)

RULES = """You write the final shortlist of jobs for one user from the candidates below.

- Assess every candidate, and every closest candidate when that block is there: one entry each, none left out, best fit first. A job that fits poorly gets a low fit_score, it is not left out: code ranks the jobs by fit_score and shows the best ones.
- job_id is the id in square brackets. fit_score is 0-100. fits: what aligns, one line. trade_offs: what the user gives up, short phrases (may be empty); name only inferred preferences, never a stated requirement. gaps: requirements the user does not meet, short phrases (may be empty).
- The stated requirements are rules: a job that misses one is never a match. Code shows the jobs that miss one (a closest candidate's "misses" line, or an unmet label) apart from the matches, as the closest jobs, with what each misses, to fill the list up to 10.
- unmet, for every job: the labels (R1, R2, ...) of the labelled stated requirements it does not meet, judged from its text (no field carries them, so only you can check them; for an avoid item, the job is what the user avoids); empty when it meets them all or none is labelled. A job with an unmet label is no match: code moves it to the closest jobs, so the summary counts as matches only the jobs without one.
- Only the stated requirements are requirements. The inferred preferences (read from the CV, the preference memory or the request) are preferences: in the summary and everywhere else, never call them requirements, and say that jobs meet "the requirements" only of the stated ones.
- Never mention job ids or short ids (the codes in square brackets) anywhere in the output text: summary, fits, trade_offs and gaps name a job by its title and company. Only job_id carries the id. Requirement labels (R1, R2, ...) go only in unmet: the text names a requirement by its words.
- A candidate's "known gaps" and "known trade-offs" lines list what its fields contradict among the inferred preferences: a language the CV does not list is a gap, another work mode, seniority or company type is a trade-off. Code adds them to the job's gaps and trade_offs: do not repeat them, but weigh them in fit_score and never explain them away.
- Judge by the job text, not the title alone.
- summary: two or three sentences on what was tried and concluded, honest about trade-offs and about how few good jobs there were, if so. When fewer than 10 jobs meet every stated requirement, say how many do and what the closest jobs mostly miss. When the posting window says it widened, the summary says so and how far back the jobs go (older postings have more applicants already)."""


class ShortlistItem(BaseModel):
    job_id: str = Field(description="the short id in square brackets")
    # before fit_score, so the model decides it first
    unmet: list[str] = Field(description="labels (R1, ...) of the labelled stated requirements it does not meet")
    fit_score: int = Field(description="0 to 100")
    fits: str
    trade_offs: list[str]
    gaps: list[str]


class FinalShortlist(BaseModel):
    # code keeps the best matches, then the closest jobs, MAX_SHORTLIST in all
    jobs: list[ShortlistItem] = Field(description=(
        "one entry for every candidate and every closest candidate, none left out, best fit first"))
    summary: str


def _brief(state: AgentState) -> Brief:
    return Brief.model_validate(state.get("brief") or {})


def _misses(c: Candidate, brief: Brief, now: datetime) -> list[str]:
    return misses(brief, c.fields(now))


def _eligible(c: Candidate, brief: Brief, now: datetime) -> bool:
    """A job may be shortlisted (or returned as partial) only if it positively meets every stated
    hard item whose field it carries (a stated window too, at `now`); a job a loosened search
    found is held to the stated items like any other."""
    return not _misses(c, brief, now)


def closest_candidates(state: AgentState, now: datetime, limit: int) -> list[Candidate]:
    """The pool's jobs that miss a stated item, fewest misses first, then the most relevant; at most `limit`."""
    brief = _brief(state)
    pool = (Candidate.from_dict(c) for c in (state.get("pool") or {}).values())
    near = [(len(m), -c.score, c) for c in pool if (m := _misses(c, brief, now))]
    return [c for *_, c in sorted(near, key=lambda t: t[:2])][:max(0, limit)]


def _closest_query(state: AgentState) -> str:
    """The run's first search query that loosened nothing (the agent's wording of the request), else the request."""
    return next((s["query"] for s in state.get("searches") or [] if not s.get("loosened")), state.get("query", ""))


async def gather_closest(state: AgentState, ctx: AgentContext, report) -> tuple[AgentState, dict]:
    """When fewer than MAX_SHORTLIST pool jobs meet the stated items and the pool holds too few
    near-misses to fill the result, one more search finds the closest jobs (search.closest_search):
    the safety net for a run whose loosened searches found too few, or that loosened nothing.

    Returns the state with its jobs in the pool, and the update that adds them (empty when no
    search ran). A failed search is logged and leaves the state as it is: the run still ends."""
    eligible = len(finalize_candidates(state, ctx.now))
    if eligible + len(closest_candidates(state, ctx.now, MAX_SHORTLIST)) >= MAX_SHORTLIST:
        return state, {}
    brief = _brief(state)
    report({"status": "Looking for the closest jobs"})
    try:
        found = await closest_search(query=_closest_query(state), brief=brief,
                                     max_age_hours=run_window(state, brief), biencoder=ctx.biencoder,
                                     reranker=ctx.reranker, os_client=ctx.os_client,
                                     session_factory=ctx.session_factory)
    except Exception:
        logger.exception("advanced closest-jobs search failed; the run ends with what it found")
        return state, {}
    taken = state.get("short_ids") or {}
    update = {"pool": {c.job_id: c.to_dict() for c in found},
              "short_ids": assign_short_ids([c.job_id for c in found], taken)}
    return {**state, "pool": merge_pool(state.get("pool"), update["pool"]),
            "short_ids": {**taken, **update["short_ids"]}}, update


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


def to_judge(brief: Brief) -> dict[str, BriefItem]:
    """The stated items no field carries ("an NLP or search role"), by label: code cannot check
    them, so finalize has the model judge each job against them and drops the jobs it says miss one."""
    return {f"R{n}": i for n, i in enumerate((i for i in brief.locked() if i.field is None), start=1)}


def _item_lines(items: list[BriefItem], labels: dict[str, BriefItem] | None = None) -> str:
    """One line per brief item: its label (to_judge) if it has one, its text, its kind when it is
    not require, and its field and value."""
    label_of = {i.text: label for label, i in (labels or {}).items()}
    lines = []
    for i in items:
        details = [] if i.kind == "require" else [i.kind]
        if i.field is not None:
            details.append(f"{i.field}: {'/'.join(map(str, i.value)) if isinstance(i.value, list) else i.value}")
        label = f"{label_of[i.text]}: " if i.text in label_of else ""
        lines.append(f"- {label}{i.text}" + (f" ({'; '.join(details)})" if details else ""))
    return "\n".join(lines) or "—"


def _candidate_block(c: Candidate, short_id: str, brief: Brief, now: datetime) -> str:
    block = f"{job_line(c, short_id, now)}\n    {c.text[:FINALIZE_TEXT_CHARS]}"
    gaps, trade_offs = inferred_shortfalls(brief, c.fields(now))
    if gaps:
        block += "\n    known gaps: " + "; ".join(gaps)
    if trade_offs:
        block += "\n    known trade-offs: " + "; ".join(trade_offs)
    return block


def _merge(code: list[str], model: list[str], owned: list[str] = ()) -> list[str]:
    """The code's phrases, then the model's, without repeats (case-insensitive) or blanks.

    `owned` are the languages the code's gaps already cover: a model phrase naming one says the
    same thing again in other words ("Spanish required" next to "requires Spanish"), so it is dropped."""
    owned_words = re.compile(r"\b(" + "|".join(map(re.escape, owned)) + r")\b", re.I) if owned else None
    merged: dict[str, str] = {}
    for phrase in [*code, *(m for m in model if not (owned_words and owned_words.search(m)))]:
        if phrase.strip():
            merged.setdefault(phrase.strip().casefold(), phrase.strip())
    return list(merged.values())


def window_summary(state: AgentState) -> str:
    """How far back the searches looked: one window, or where they started and how far they widened."""
    hours = [s["max_age_hours"] for s in state.get("searches") or [] if s.get("max_age_hours") is not None]
    if not hours:
        return "—"
    if max(hours) == hours[0]:
        return f"last {window_text(hours[0])} throughout"
    return f"started at last {window_text(hours[0])}, widened to last {window_text(max(hours))}"


def _closest_block(c: Candidate, short_id: str, brief: Brief, now: datetime) -> str:
    return _candidate_block(c, short_id, brief, now) + "\n    misses: " + "; ".join(_misses(c, brief, now))


def finalize_prompt(state: AgentState, ctx: AgentContext, candidates: list[Candidate],
                    closest: list[Candidate] = ()) -> list[BaseMessage]:
    brief = _brief(state)
    by_job = {job_id: short for short, job_id in (state.get("short_ids") or {}).items()}

    def short(c: Candidate) -> str:
        return by_job.get(c.job_id, c.job_id[:4])

    stated = [i for i in brief.items if i.source == "stated"]
    inferred = [i for i in brief.items if i.source == "inferred"]
    blocks = [
        ctx.cv_section(),
        f"Preference memory (inferred):\n{ctx.preference_memory or '—'}",
        "Stated requirements (said by the user; every result must meet these; the only requirements):\n"
        + _item_lines(stated, to_judge(brief)),
        "Inferred preferences (not requirements; you may trade them):\n" + _item_lines(inferred),
        f"Brief notes:\n{brief.notes or '—'}",
        f"Agent notes:\n{state.get('notes') or '—'}",
        "Candidates:\n" + ("\n".join(_candidate_block(c, short(c), brief, ctx.now) for c in candidates) or "—"),
        *(["Closest candidates (fewer than 10 candidates meet every stated requirement; each of these misses "
           "the ones on its misses line):\n" + "\n".join(_closest_block(c, short(c), brief, ctx.now) for c in closest)]
          if closest else []),
        "Searches:\n" + ("\n".join(s["header"] for s in state.get("searches") or []) or "—"),
        "Posting window:\n" + window_summary(state),
        # A free-text answer the agent did not write into the brief still counts.
        "Questions and the user's answers:\n" + (
            "\n".join(f"- Q: {q['question']} A: {q['answer']}" for q in state.get("qa") or []) or "—"),
    ]
    return [SystemMessage(RULES), HumanMessage("\n\n".join(blocks))]


def _scored(item: ShortlistItem, c: Candidate, brief: Brief, now: datetime) -> dict:
    """A result entry from the model's item: its fit, and the code's gaps and trade-offs first."""
    fields = c.fields(now)
    gaps, trade_offs = inferred_shortfalls(brief, fields)
    owned = missing_languages(brief, fields)
    return {**item.model_dump(exclude={"unmet"}), "job_id": c.job_id, "fit_score": max(0, min(100, item.fit_score)),
            "gaps": _merge(gaps, item.gaps, owned), "trade_offs": _merge(trade_offs, item.trade_offs, owned)}


def _unscored(c: Candidate, brief: Brief, now: datetime, unchecked: list[str] = ()) -> dict:
    """An entry no model scored: no fit fields; the code's gaps, trade-offs and misses (plus `unchecked`)."""
    gaps, trade_offs = inferred_shortfalls(brief, c.fields(now))
    return {"job_id": c.job_id, "fit_score": None, "fits": "", "trade_offs": trade_offs, "gaps": gaps,
            "misses": [*_misses(c, brief, now), *unchecked]}


def _unlabel(text: str, judged: dict[str, BriefItem]) -> str:
    """The text without requirement labels, which the user never sees: "(R1)" is dropped, a bare
    "R1" becomes the requirement's words. Labels no item has are left as they are."""
    text = re.sub(r"\s*\((R\d+)\)", lambda m: "" if m[1] in judged else m[0], text)
    return re.sub(r"\b(R\d+)\b", lambda m: f'"{judged[m[1]].text}"' if m[1] in judged else m[0], text)


def _shown_closest(matches: list[dict], closest: list[dict]) -> list[dict]:
    """The closest jobs that fill the result up to MAX_SHORTLIST: the fewest misses first, then the best fit."""
    return sorted(closest, key=lambda r: (len(r["misses"]), -(r["fit_score"] or 0)))[:max(0, MAX_SHORTLIST - len(matches))]


async def run_finalize(state: AgentState, ctx: AgentContext, timeout: float) -> dict:
    """The model assesses every eligible job, and the closest ones that fill the result up to
    MAX_SHORTLIST: a fit score and the labelled requirements it misses. Code ranks them by fit score and
    splits them into the matches and the closest jobs, each with what it misses."""
    brief = _brief(state)
    candidates = finalize_candidates(state, ctx.now)
    near = closest_candidates(state, ctx.now, MAX_SHORTLIST - len(candidates))
    by_job = {c.job_id: c for c in [*candidates, *near]}
    short_ids = state.get("short_ids") or {}
    judged = to_judge(brief)
    result: FinalShortlist = await ctx.llm.structured(finalize_prompt(state, ctx, candidates, near), FinalShortlist,
                                                      FINALIZE_MAX_TOKENS, timeout=timeout)
    assessed: dict[str, dict] = {}
    for item in result.jobs:
        job_id = short_ids.get(item.job_id.strip().strip("[]").strip().lower())
        c = by_job.get(job_id) if job_id else None
        if c is None or job_id in assessed:
            continue
        item = item.model_copy(update={"fits": _unlabel(item.fits, judged),
                                       "gaps": [_unlabel(g, judged) for g in item.gaps],
                                       "trade_offs": [_unlabel(t, judged) for t in item.trade_offs]})
        flagged = {u.strip().strip("[]").upper() for u in item.unmet}  # unknown labels are ignored
        missed = [*_misses(c, brief, ctx.now), *(filter_label(i) for label, i in judged.items() if label in flagged)]
        assessed[job_id] = {**_scored(item, c, brief, ctx.now), "misses": missed}
    # A job the model left out comes unscored, after the scored ones, and misses the labelled items
    # (R1, R2, ...) it never judged: it is a match only when there are none.
    unchecked = [f"{filter_label(i)} (not confirmed)" for i in judged.values()]
    ranked = [*sorted(assessed.values(), key=lambda r: -r["fit_score"]),
              *(_unscored(c, brief, ctx.now, unchecked) for job_id, c in by_job.items() if job_id not in assessed)]
    matches = [r for r in ranked if not r["misses"]][:MAX_SHORTLIST]
    closest = _shown_closest(matches, [r for r in ranked if r["misses"]])
    return {"status": "done", "shortlist": matches, "closest": closest, "summary": _unlabel(result.summary, judged)}


def partial_result(state: AgentState, now: datetime) -> dict:
    """The most relevant eligible jobs, then the closest ones up to MAX_SHORTLIST in all, without
    fit fields; gaps, trade-offs and misses are the code's own. The summary is empty: no LLM wrote
    one, and `status: partial` already says the run did not finish."""
    brief = _brief(state)
    pool = (Candidate.from_dict(c) for c in (state.get("pool") or {}).values())
    ranked = sorted((c for c in pool if _eligible(c, brief, now)), key=lambda c: -c.score)
    shortlist = [_unscored(c, brief, now) for c in ranked[:MAX_SHORTLIST]]
    closest = [_unscored(c, brief, now) for c in closest_candidates(state, now, MAX_SHORTLIST - len(shortlist))]
    return {"status": "partial", "shortlist": shortlist, "closest": closest, "summary": ""}


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
