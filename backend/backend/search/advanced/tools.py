"""The five tools of the advanced-search agent, and the graph's tools step that runs them.

Each tool returns a Command whose update carries the ToolMessage, or a plain string (a refusal
the model reads as the tool result). Budgets and locked requirements are enforced here, in code.

The tools read budgets and the brief from one state snapshot, so they are only safe one call at
a time: `tools_step`, the graph's "tools" node, runs the first tool call of a turn and refuses
the others (the agent model is also bound with parallel_tool_calls=False).
"""
import logging

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolNode, ToolRuntime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from backend.search.advanced.brief import Brief, BriefItem, LockedRequirementError, hard_filters, merge_brief, relax
from backend.search.advanced.budget import (
    CANDIDATE_WINDOW,
    INSPECT_CHARS,
    MAX_INSPECT,
    MAX_PROBES,
    MAX_QUESTIONS,
    MAX_SEARCHES,
    MAX_VARIANTS,
)
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import assign_short_ids, header_line, render_observation
from backend.search.advanced.search import SearchVariant, matches_soft, run_variant
from backend.search.advanced.state import AgentState, merge_pool
from backend.search.filters import SearchFilters

logger = logging.getLogger(__name__)

Runtime = ToolRuntime[AgentContext, AgentState]


def _brief(state: AgentState) -> Brief:
    return Brief.model_validate(state.get("brief") or {})


def _reply(runtime: Runtime, content: str, **update) -> Command:
    return Command(update={**update, "messages": [ToolMessage(content, tool_call_id=runtime.tool_call_id)]})


def _locked_dict(locked: SearchFilters | None) -> dict:
    if locked is None:
        return {}
    values = locked.model_dump(exclude_none=True, exclude={"semantic_query", "strict", "max_age_hours"})
    if "country" in values:
        values["company_country"] = values.pop("country")
    return values


@tool
def update_brief(items: list[BriefItem], runtime: Runtime, notes: str = "") -> Command | str:
    """Write the whole brief: every requirement you work with, replacing the previous brief.

    Each item is a short phrase (`text`), `kind` require / prefer / avoid, and `source`: "stated"
    when the user said it (in the request or an answer), "inferred" when you read it into the CV,
    the preference memory or the request. When unsure, it is stated. Give `field` and `value`
    when the item maps to an index field. Stated items are locked: you cannot drop or change
    them; only the user can, through ask_user.
    """
    state = runtime.state
    current, proposed = _brief(state), Brief(items=items, notes=notes)
    unlock_allowed = state.get("unlock_turn") == state.get("turns", 0) - 1
    try:
        brief = merge_brief(current, proposed, unlock_allowed=unlock_allowed)
    except LockedRequirementError as e:
        return f"Refused: '{e.item.text}' was stated by the user; only the user can relax it (ask_user)."
    runtime.stream_writer({"type": "brief", "brief": brief.to_api()})
    return _reply(runtime, "ok", brief=brief.model_dump())


def _refusal(variant: SearchVariant, brief: Brief, searches: int, probes: int) -> str | None:
    if searches >= MAX_SEARCHES:
        return f"search budget of {MAX_SEARCHES} is spent"
    if variant.probe is None:
        return None
    if probes >= MAX_PROBES:
        return f"probe budget of {MAX_PROBES} is spent"
    item = next((i for i in brief.locked() if i.text.casefold() == variant.probe.casefold()), None)
    if item is None:
        return (f'probe "{variant.probe}" is not a stated requirement of the brief; '
                "a probe lifts one stated item, named by its exact text")
    if item.field is None:
        return (f'"{item.text}" maps to no index field, so no search can lift it; '
                "check it on the job lines instead")
    if item.kind == "prefer":
        return f'"{item.text}" is a stated preference, not a hard filter; there is nothing to lift'
    return None


@tool
async def search_jobs(variants: list[SearchVariant], runtime: Runtime) -> Command | str:
    """Run 1-3 searches; each returns counts over its top 30 jobs and the top 8 job lines.

    Per variant: `query` (semantic text), `filters` (soft: they rank matches higher but do not
    exclude), `max_age_hours` (posting age window, default 72) and `probe`. The brief's stated
    requirements are added as hard filters to every variant; do not repeat them. `probe` names
    one stated item's exact text to lift it, for a what-if count to ask the user about; probe
    jobs are flagged with what they break. `company_country` is where the company is based, not
    the job: check job locations in the lines.
    """
    if not 1 <= len(variants) <= MAX_VARIANTS:
        return f"Refused: give 1 to {MAX_VARIANTS} variants (at most {MAX_VARIANTS} per call)."
    ctx, state = runtime.context, runtime.state
    brief = _brief(state)
    searches_run, probes_run = state.get("searches_run", 0), state.get("probes_run", 0)
    index = len(state.get("searches", []))
    pool = dict(state.get("pool") or {})
    short_ids = dict(state.get("short_ids") or {})
    new_pool: dict[str, dict] = {}
    new_short: dict[str, str] = {}
    log: list[dict] = []
    ran = probes = 0
    parts: list[str] = []
    for variant in variants:
        if refusal := _refusal(variant, brief, searches_run + ran, probes_run + probes):
            parts.append(f'Refused "{variant.query}": {refusal}.')
            continue
        try:  # the probe is already checked: any error here is unexpected
            candidates = await run_variant(
                variant=variant, brief=brief, search_index=index + 1, biencoder=ctx.biencoder,
                reranker=ctx.reranker, os_client=ctx.os_client, session_factory=ctx.session_factory,
            )
        except Exception as e:  # a failed search is reported, the others still run
            logger.exception("advanced search variant failed")
            parts.append(f'Search "{variant.query}" failed: {type(e).__name__}: {e}')
            continue
        index += 1
        ran += 1
        probes += variant.probe is not None
        found = {c.job_id: c.to_dict() for c in candidates}
        new_pool = merge_pool(new_pool, found)
        pool = merge_pool(pool, found)
        added = assign_short_ids([c.job_id for c in candidates], short_ids)
        short_ids |= added
        new_short |= added
        locked = hard_filters(brief, lifted=variant.probe)
        observation = render_observation(index, variant, locked, candidates, short_ids, len(pool), ctx.now)
        header = header_line(observation)
        log.append({
            "index": index,
            "query": variant.query,
            "filters": variant.filters.model_dump(exclude_none=True) | {"locked": _locked_dict(locked)},
            "max_age_hours": variant.max_age_hours,
            "probe": variant.probe,
            "matches": sum(matches_soft(c, variant) for c in candidates[:CANDIDATE_WINDOW]),
            "header": header,
        })
        runtime.stream_writer({"type": "search", "line": header})
        parts.append(observation)
    return _reply(runtime, "\n\n".join(parts), pool=new_pool, short_ids=new_short, searches=log,
                  searches_run=ran, probes_run=probes)


@tool
def inspect_job(ids: list[str], runtime: Runtime) -> Command | str:
    """Read up to 3 jobs' summaries (short ids from the job lines), when a line is not enough to judge."""
    if len(ids) > MAX_INSPECT:
        return f"Refused: at most {MAX_INSPECT} ids per call."
    pool, short_ids = runtime.state.get("pool") or {}, runtime.state.get("short_ids") or {}
    parts, shown = [], []
    for short in ids:
        job = pool.get(short_ids.get(short, ""))
        if job is None:
            parts.append(f"Unknown id: {short}")
            continue
        head = " · ".join(p for p in (job["title"], job.get("company")) if p)
        parts.append(f"[{short}] {head}\n{(job.get('text') or '')[:INSPECT_CHARS]}")
        shown.append(short)
    if shown:
        runtime.stream_writer({"type": "inspect", "ids": shown})
    return _reply(runtime, "\n\n".join(parts))


class QuestionOption(BaseModel):
    label: str = Field(description="The answer as the user sees it, e.g. 'Allow hybrid' or 'Keep remote only'")
    relax: list[str] = Field(
        default=[], description="Texts of the stated items this answer relaxes to inferred; empty to keep them"
    )


def _answer_text(value) -> str:
    """The resume value as text: a string, or the `answer` of a {"answer": ...} dict.

    Anything else is a caller bug; str() keeps the run going rather than failing the turn.
    """
    if isinstance(value, dict) and isinstance(value.get("answer"), str):
        value = value["answer"]
    return value if isinstance(value, str) else str(value)


@tool
def ask_user(question: str, options: list[QuestionOption], runtime: Runtime) -> Command | str:
    """Ask the user one question and wait for the answer: a chosen option or free text.

    Only to relax a stated requirement that keeps results thin or weak (with numbers from a
    probe, and always an option that keeps it), or to settle a trade-off between inferred
    preferences that the evidence cannot. Never at the start, never twice on the same point.
    A chosen option's `relax` is applied for you; after a free-text answer you may change
    stated items in your next update_brief.
    """
    state = runtime.state
    if state.get("questions_asked", 0) >= MAX_QUESTIONS:
        return "No more questions; decide with what you have."
    if not options:
        return "Refused: a question needs options, including one that keeps things as they are."
    brief = _brief(state)
    stated = {i.text.casefold() for i in brief.locked()}
    for o in options:
        if unknown := [t for t in o.relax if t.casefold() not in stated]:
            return (f"Refused: option '{o.label}' relaxes '{unknown[0]}', which is not a stated item of the "
                    "brief; name stated items by their exact text.")
    labels = [o.label for o in options]
    # The tool re-runs from the top on resume: nothing may happen before interrupt().
    answer = _answer_text(interrupt({"question": question, "options": labels}))
    chosen = next((o for o in options if o.label.strip().casefold() == answer.strip().casefold()), None)
    qa = {"question": question, "options": labels, "answer": answer, "chosen": chosen.label if chosen else None}
    if chosen is None:
        return _reply(runtime, f"User answered: {answer}", questions_asked=1, qa=[qa],
                      unlock_turn=state.get("turns", 0))
    update = {}
    if chosen.relax:
        brief = relax(brief, chosen.relax)
        runtime.stream_writer({"type": "brief", "brief": brief.to_api()})
        update["brief"] = brief.model_dump()
    return _reply(runtime, f"User chose: {chosen.label}", questions_asked=1, qa=[qa], **update)


@tool
def finish(keep: list[str], notes: str, runtime: Runtime) -> Command:
    """Stop searching. `keep`: up to 10 short ids worth shortlisting, from anything you have seen;
    `notes`: a sentence or two on what you tried and concluded."""
    short_ids = runtime.state.get("short_ids") or {}
    job_ids = list(dict.fromkeys(short_ids[s] for s in keep if s in short_ids))
    unknown = [s for s in keep if s not in short_ids]
    content = "ok" + (f"; unknown ids dropped: {', '.join(unknown)}" if unknown else "")
    return _reply(runtime, content, keep=job_ids, notes=notes, finished=True)


TOOLS: list[BaseTool] = [update_brief, search_jobs, inspect_job, ask_user, finish]

ONE_CALL_REFUSAL = "Refused: one tool call per turn — call it again next turn."

_TOOL_NODE = ToolNode(TOOLS)


async def tools_step(state: AgentState, config: RunnableConfig):
    """The graph's "tools" node: runs the first tool call of the last AIMessage and refuses the rest.

    Every tool call id gets exactly one ToolMessage. The inner ToolNode gets the node's config, so
    the run context, stream writer and interrupts reach the tools as usual.
    """
    messages = state["messages"]
    last = next(m for m in reversed(messages) if isinstance(m, AIMessage))
    if len(last.tool_calls) <= 1:
        return await _TOOL_NODE.ainvoke(state, config)
    first, *rest = last.tool_calls
    trimmed = last.model_copy(update={"tool_calls": [first]})
    output = await _TOOL_NODE.ainvoke({**state, "messages": [trimmed if m is last else m for m in messages]}, config)
    refusals = [ToolMessage(ONE_CALL_REFUSAL, tool_call_id=c["id"], name=c["name"], status="error") for c in rest]
    if isinstance(output, dict):  # the first tool returned a plain message
        return {**output, "messages": [*output["messages"], *refusals]}
    return [*output, {"messages": refusals}]  # Commands from the first tool, then the refusals
