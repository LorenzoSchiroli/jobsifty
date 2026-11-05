"""The agent's system prompt and the compaction of its history."""
import json

from langchain_core.messages import AnyMessage, ToolMessage

from backend.search.advanced.brief import Brief
from backend.search.advanced.budget import CV_CHARS, MAX_PROBES, MAX_QUESTIONS, MAX_SEARCHES, MAX_TURNS
from backend.search.advanced.context import AgentContext
from backend.search.advanced.observation import Candidate
from backend.search.advanced.state import AgentState

RULES = """You are a job-search agent. You find the jobs that fit one user best, using tools over a job index.

How to work
- Call update_brief first, to write down what the user wants. Then search, look, and finish.
- One tool call per turn. search_jobs takes up to 3 variants in that one call; use them to try different wordings or filters side by side.
- search_jobs: the brief's stated requirements are added to every search as hard filters; do not repeat them. Your own filters are soft (they rank, they do not exclude). You see counts over the top 30 jobs and the top 8 job lines of each search.
- inspect_job: read a few jobs in more detail before you keep them.
- ask_user: a short question with options. finish: name the job ids worth considering (keep) and your notes; a final step then writes the shortlist from everything found.

Stated and inferred requirements
- Stated requirements (said by the user, in the request or in an answer) are LOCKED: every result keeps them, and only the user relaxes them. You may drop or change a stated item only after the user chose an ask_user option that relaxes it, or after a free-text answer, and then in the very next update_brief.
- When you are unsure whether the user stated something, treat it as stated.
- A mapped item's value is one of the index values in the tool schema; "remote or hybrid", "mid or senior" is one item with a list of values.
- A preference (kind prefer, "preferably remote") is never locked, even when the user said it: it is always inferred, and you may trade it for better results.
- Inferred requirements (your reading of the CV, the preference memory, what the user probably wants) you may trade. The preference memory counts as inferred: the request always wins over it.
- Prefer fewer results to results that break a stated requirement.

When to ask
- Use a probe (a search_jobs variant that lifts one stated item) only to get numbers for a question you are about to ask.
- Ask only to relax a stated requirement or to settle a trade-off the results show (for example "only 2 remote senior jobs this week; 14 if you accept hybrid"), with the numbers and an option that keeps things as they are. Never ask at the start, before you have searched.
- Report trade-offs honestly; do not hide that results fall short.
"""


def _brief_text(state: AgentState) -> str:
    brief = Brief.model_validate(state.get("brief") or {})
    return json.dumps(brief.to_api(), ensure_ascii=False, separators=(",", ":"))


def pool_digest(state: AgentState) -> str:
    pool = state.get("pool") or {}
    by_job = {job_id: short for short, job_id in (state.get("short_ids") or {}).items()}
    top = sorted((Candidate.from_dict(c) for c in pool.values()), key=lambda c: -c.score)[:3]
    best = ", ".join(f"[{by_job.get(c.job_id, c.job_id[:4])}] {c.title} ({c.score:.2f})" for c in top)
    return f"pool: {len(pool)} jobs" + (f", best: {best}" if best else "")


def system_prompt(state: AgentState, ctx: AgentContext) -> str:
    left = (
        f"turns {max(0, MAX_TURNS - state.get('turns', 0))}, "
        f"searches {max(0, MAX_SEARCHES - state.get('searches_run', 0))}, "
        f"probes {max(0, MAX_PROBES - state.get('probes_run', 0))}, "
        f"questions {max(0, MAX_QUESTIONS - state.get('questions_asked', 0))}"
    )
    return "\n\n".join([
        RULES,
        f"CV:\n{ctx.cv_text[:CV_CHARS]}",
        f"Preference memory (inferred):\n{ctx.preference_memory or '—'}",
        f"Brief (stated items are locked):\n{_brief_text(state)}",
        f"Budget left: {left}",
        pool_digest(state),
    ])


def compact_history(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Older search_jobs results shrink to the header line of each observation; the last stays whole."""
    last = max((i for i, m in enumerate(messages) if isinstance(m, ToolMessage) and m.name == "search_jobs"),
               default=None)
    out: list[AnyMessage] = []
    for i, m in enumerate(messages):
        if isinstance(m, ToolMessage) and m.name == "search_jobs" and i != last:
            headers = [part.splitlines()[0] for part in str(m.content).split("\n\n") if part.strip()]
            m = ToolMessage("\n".join(headers), tool_call_id=m.tool_call_id, name=m.name)
        out.append(m)
    return out
