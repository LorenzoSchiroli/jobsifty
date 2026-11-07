import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_pool(left: dict[str, dict] | None, right: dict[str, dict] | None) -> dict[str, dict]:
    """Pool entries by job_id; a job found twice keeps its higher-scored entry."""
    merged = dict(left or {})
    for job_id, new in (right or {}).items():
        old = merged.get(job_id)
        merged[job_id] = new if old is None or new["score"] > old["score"] else old
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
    brief_written: bool  # update_brief has run: search_jobs is refused before
    pool: Annotated[dict[str, dict], merge_pool]  # job_id -> Candidate.to_dict()
    short_ids: Annotated[dict[str, str], merge_dicts]  # short id -> job_id
    searches: Annotated[list[dict], operator.add]
    searches_run: Annotated[int, operator.add]
    window_hours: int  # the widest posting window a search of the run used (tools.run_window)
    questions_asked: Annotated[int, operator.add]
    turns: Annotated[int, operator.add]
    unlock_turn: int  # the turn of a free-text answer; the next turn may change locked items
    qa: Annotated[list[dict], operator.add]
    keep: list[str]  # job ids
    notes: str
    finished: bool
    status: str
    shortlist: list[dict]  # the matches: jobs meeting every stated requirement
    closest: list[dict]  # the closest jobs filling the result up to MAX_SHORTLIST, each with its misses
    summary: str
    usage: Annotated[dict, add_usage]  # {"llm_calls", "input_tokens", "output_tokens"}

