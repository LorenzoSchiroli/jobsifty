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
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError

from backend.search.advanced.brief import (
    Brief,
    BriefItem,
    MAX_ITEM_WORDS,
    LockedRequirementError,
    bundled,
    filter_label,
    hard_filters,
    merge_brief,
    inferred_window,
    misses,
    relax,
    stated_window,
)
from backend.search.advanced.budget import (
    CANDIDATE_WINDOW,
    INSPECT_CHARS,
    MAX_INSPECT,
    MAX_QUESTIONS,
    MAX_SEARCHES,
    MAX_TURNS,
    MAX_VARIANTS,
    START_WINDOW_HOURS,
    live_window_hours,
)
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import assign_short_ids, brief_names, header_line, render_observation
from backend.search.advanced.search import SearchVariant, run_variant, soft_matches
from backend.search.advanced.state import AgentState, merge_pool
from backend.search.filters import window_label

logger = logging.getLogger(__name__)

Runtime = ToolRuntime[AgentContext, AgentState]


def _brief(state: AgentState) -> Brief:
    return Brief.model_validate(state.get("brief") or {})


def _reply(runtime: Runtime, content: str, **update) -> Command:
    return Command(update={**update, "messages": [ToolMessage(content, tool_call_id=runtime.tool_call_id)]})


@tool
def update_brief(items: list[BriefItem], runtime: Runtime, notes: str = "") -> Command | str:
    """Write the whole brief: every requirement you work with, replacing the previous brief.

    Each item is one requirement in a few words (`text`): never a list or the whole request.
    "junior remote NLP engineer in Munich, no consulting" is five items: junior, remote, an NLP
    role, in Munich, no consulting. Each has `kind` require / prefer / avoid, and `source`: "stated"
    when the user said it (in the request or an answer), "inferred" when you read it into the CV,
    the preference memory or the request. When unsure, it is stated, except a `prefer` item, which
    is always inferred: preferences are never locked. Give `field` and `value`
    when the item maps to an index field, with a value from the schema's vocabulary; "remote or
    hybrid" is one item whose value is ["remote", "hybrid"], never two items on one field.
    Stated items are locked: you cannot drop or change them; only the user can, through ask_user.
    Call it before the first search_jobs.
    """
    state = runtime.state
    try:
        proposed = Brief(items=items, notes=notes)
    except ValidationError as e:
        return "Refused: " + "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
    if lumped := [i.text for i in proposed.items if bundled(i, state.get("query", ""))]:
        return ("Refused: " + "; ".join(f"'{t}'" for t in lumped) + " holds several requirements. Give one item "
                f"per requirement, at most {MAX_ITEM_WORDS} words and no commas: 'junior', 'remote', 'in Munich'.")
    current = _brief(state)
    unlock_allowed = state.get("unlock_turn") == state.get("turns", 0) - 1
    try:
        brief = merge_brief(current, proposed, unlock_allowed=unlock_allowed)
    except LockedRequirementError as e:
        return f"Refused: '{e.item.text}' was stated by the user; only the user can relax it (ask_user)."
    runtime.stream_writer({"status": "Noting what you asked for"})
    return _reply(runtime, "ok", brief=brief.model_dump(), brief_written=True)


def _refusal(variant: SearchVariant, brief: Brief, searches: int) -> str | None:
    if searches >= MAX_SEARCHES:
        return f"search budget of {MAX_SEARCHES} is spent"
    stated = {i.field for i in brief.locked() if i.field is not None}
    if missing := [l.field for l in variant.loosen if l.field not in stated]:
        return (f"nothing stated on {', '.join(missing)} to loosen; loosen names the field of a stated item "
                "(a soft filter ranks by anything else)")
    return None


def run_window(state: AgentState, brief: Brief) -> int:
    """The posting window of a search whose variant gives none: the user's stated window, else the
    widest window the run has used, at least the starting one (the window the preference memory
    or the CV states, else START_WINDOW_HOURS). Never wider than the live window."""
    if (stated := stated_window(brief)) is not None:
        return min(stated.value * 24, live_window_hours())
    remembered = inferred_window(brief)
    start = min(remembered.value * 24 if remembered else START_WINDOW_HOURS, live_window_hours())
    return max(start, state.get("window_hours") or 0)


def _loosened(variant: SearchVariant, brief: Brief) -> list[str]:
    """What the variant eases, in the filters panel's words: "in Munich → in Munich, Austria or
    Switzerland", "without remote", and a window wider than the stated one."""
    by_field = {i.field: i for i in brief.locked() if i.field is not None}
    out = []
    for l in variant.loosen:
        item = by_field[l.field]
        out.append(f"without {filter_label(item)}" if l.value is None
                   else f"{filter_label(item)} → {filter_label(item.model_copy(update={'value': l.value}))}")
    stated = stated_window(brief)
    if stated is not None and variant.max_age_hours > stated.value * 24:
        out.append(f"posted in the {window_label(stated.value * 24)} → {window_label(variant.max_age_hours)}")
    return out


@tool
async def search_jobs(variants: list[SearchVariant], runtime: Runtime) -> Command | str:
    """Run 1-3 searches; each returns counts over its top 30 jobs and the top 8 job lines.

    Per variant: `query` (semantic text), `filters` (soft: they rank matches higher but do not
    exclude), `max_age_hours` (posting age window, up to the live window; leave it out for the
    run's window: the user's stated one, else the widest used so far, starting at 72 or the
    remembered window) and `loosen`. The brief's stated requirements are added as hard filters to
    every variant; do not repeat them. `loosen` eases stated items for this one search: each names
    a stated item's field and the nearer value to search on (place ["Munich"] -> ["Munich",
    "Augsburg", "Austria", "Switzerland"]), or no value to drop the filter; a max_age_hours wider
    than a stated window loosens it too. A job that misses a stated item is never a match: it
    fills the result among the closest jobs. `place` is where the job is; `company_country` is
    where the company is based.
    """
    ctx, state = runtime.context, runtime.state
    if not state.get("brief_written"):
        return ("Refused: call update_brief first, to write down what the user stated and what you infer; "
                "the stated requirements become the hard filters of every search.")
    if not 1 <= len(variants) <= MAX_VARIANTS:
        return f"Refused: give 1 to {MAX_VARIANTS} variants (at most {MAX_VARIANTS} per call)."
    brief = _brief(state)
    searches_run = state.get("searches_run", 0)
    index = len(state.get("searches", []))
    pool = dict(state.get("pool") or {})
    short_ids = dict(state.get("short_ids") or {})
    new_pool: dict[str, dict] = {}
    new_short: dict[str, str] = {}
    log: list[dict] = []
    default_hours = run_window(state, brief)
    widest = default_hours
    ran = 0
    parts: list[str] = []
    for variant in variants:
        if refusal := _refusal(variant, brief, searches_run + ran):
            parts.append(f'Refused "{variant.query}": {refusal}.')
            continue
        variant = variant.model_copy(update={"max_age_hours": variant.max_age_hours or default_hours})
        loosened = _loosened(variant, brief)
        eased = " · loosened" if loosened else ""
        runtime.stream_writer({"status": f'Searching "{variant.query}"{eased} · {window_label(variant.max_age_hours)}'})
        try:  # the loosened fields are already checked: any error here is unexpected
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
        widest = max(widest, variant.max_age_hours)
        found = {c.job_id: c.to_dict() for c in candidates}
        new_jobs = sum(c.job_id not in pool for c in candidates[:CANDIDATE_WINDOW])  # before this search
        new_pool = merge_pool(new_pool, found)
        pool = merge_pool(pool, found)
        added = assign_short_ids([c.job_id for c in candidates], short_ids)
        short_ids |= added
        new_short |= added
        locked = hard_filters(brief, loosened=variant.loosened())
        strict = sum(not misses(brief, c.fields(ctx.now)) for c in candidates[:CANDIDATE_WINDOW])
        observation = render_observation(index, variant, locked, candidates, short_ids, len(pool), ctx.now,
                                         strict, loosened)
        log.append({
            "index": index,
            "query": variant.query,
            "filters": variant.filters.model_dump(exclude_none=True) | {"locked": brief_names(locked)},
            "max_age_hours": variant.max_age_hours,
            "loosened": loosened,
            # jobs of the window meeting every soft filter; None when the search set none
            "matches": soft_matches(variant, candidates[:CANDIDATE_WINDOW]),
            "new_jobs": new_jobs,  # jobs of the window not in the pool before this search
            "header": header_line(observation),
        })
        parts.append(observation)
    return _reply(runtime, "\n\n".join(parts), pool=new_pool, short_ids=new_short, searches=log,
                  searches_run=ran, window_hours=widest)


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
        runtime.stream_writer({"status": f"Reading {len(shown)} job description{'s' if len(shown) > 1 else ''}"})
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

    To relax a stated requirement when results are thin and the way to ease it is not obvious
    (giving up remote, another seniority or language; a nearer place or a wider window you may
    try yourself with a loosened search), with numbers from a loosened search and always an
    option that keeps it; or to settle a trade-off between inferred preferences that the
    evidence cannot. Never at the start, never twice on the same point.
    A chosen option's `relax` is applied for you; after a free-text answer you may change
    stated items in your next update_brief.
    """
    state = runtime.state
    if state.get("questions_asked", 0) >= MAX_QUESTIONS:
        return "No more questions; decide with what you have."
    if state.get("turns", 0) >= MAX_TURNS:
        return "No turns left to act on an answer; finish with what you have."
    if not state.get("searches_run", 0):
        return "Refused: never ask at the start; search first, so the question can rest on what the results show."
    if not options:
        return "Refused: a question needs options, including one that keeps things as they are."
    if any(o.relax for o in options) and all(o.relax for o in options):
        return "Refused: a question that relaxes a requirement needs an option with no relax, to keep it as it is."
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


def _tool_error(e: Exception) -> str:
    """The tool result of a tool that raised, so the agent can take another step.

    ToolNode calls it inside its `except`, and re-raises GraphBubbleUp (interrupts) before it,
    so ask_user still pauses the run.
    """
    if isinstance(e, ToolInvocationError):  # arguments that do not validate: the message says what is wrong
        return e.message
    logger.exception("advanced agent tool raised")
    return f"Error: the tool failed ({type(e).__name__}: {e}). Try another step."


_TOOL_NODE = ToolNode(TOOLS, handle_tool_errors=_tool_error)


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
