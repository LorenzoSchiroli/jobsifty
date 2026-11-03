import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_pool(left: dict[str, dict] | None, right: dict[str, dict] | None) -> dict[str, dict]:
    """Pool entries by job_id; a job found twice keeps its higher-scored entry.

    breaks stays only while every search that found the job was a probe breaking something:
    one normal search (empty breaks) clears it, two probes give the union. Clearing is consistent
    because every search applies all the other stated requirements as hard filters: a normal
    search's job meets them all, and a probe's job with empty breaks met the lifted one too.
    """
    merged = dict(left or {})
    for job_id, new in (right or {}).items():
        old = merged.get(job_id)
        if old is None:
            merged[job_id] = new
            continue
        best = new if new["score"] > old["score"] else old
        old_breaks, new_breaks = old.get("breaks") or [], new.get("breaks") or []
        breaks = [] if not (old_breaks and new_breaks) else list(dict.fromkeys([*old_breaks, *new_breaks]))
        merged[job_id] = {**best, "breaks": breaks}
    return merged


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


def add_usage(left: dict | None, right: dict | None) -> dict:
    """Sums the llm_calls / input_tokens / output_tokens counters."""
    total = dict(left or {})
    for key, value in (right or {}).items():
        total[key] = total.get(key, 0) + value
    return total


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    query: str
    brief: dict  # a Brief dump
    pool: Annotated[dict[str, dict], merge_pool]  # job_id -> Candidate.to_dict()
    short_ids: Annotated[dict[str, str], merge_dicts]  # short id -> job_id
    searches: Annotated[list[dict], operator.add]
    searches_run: Annotated[int, operator.add]
    probes_run: Annotated[int, operator.add]
    questions_asked: Annotated[int, operator.add]
    turns: Annotated[int, operator.add]
    unlock_turn: int  # the turn of a free-text answer; the next turn may change locked items
    qa: Annotated[list[dict], operator.add]
    keep: list[str]  # job ids
    notes: str
    finished: bool
    status: str
    shortlist: list[dict]
    summary: str
    usage: Annotated[dict, add_usage]  # {"llm_calls", "input_tokens", "output_tokens"}

