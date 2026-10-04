import json
import re
import logging
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

_log = logging.getLogger(__name__)

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def large_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ["GROQ_MODEL_LARGE"],
        api_key=os.environ["GROQ_API_KEY"],
        base_url=_GROQ_BASE_URL,
    )


def _parse_json(raw: str):
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw.strip())
    return json.loads(cleaned)


_CLARIFY_SYSTEM = (
    "You help refine a job search. Given a user's query, their CV, and known preferences, "
    "produce AT MOST 2 short clarifying questions that would most improve the search. "
    "If the query is already clear, return fewer or an empty list. "
    "Return ONLY a JSON array of question strings, no prose, no markdown fences."
)

_CRITIQUE_SYSTEM = (
    "You judge whether a set of retrieved job results matches the user's intent. "
    "If the results look weak or off-target, propose ONE improved semantic search query. "
    'Return ONLY a JSON object: {"need_refine": boolean, "refined_query": string or null}. '
    "Set need_refine=false and refined_query=null when results are already good."
)

_FIT_SYSTEM = (
    "You evaluate how well each job fits the applicant, using their CV and preferences. "
    "For each job return a fit_score 0-100, a one-sentence fit_rationale, and a short fit_gaps "
    "note (skills/requirements the applicant would need to address; empty string if none). "
    'Return ONLY a JSON array of objects: '
    '{"job_id": string, "fit_score": integer, "fit_rationale": string, "fit_gaps": string}.'
)

_DISTILL_SYSTEM = (
    "You maintain a short natural-language summary of a job seeker's preferences, learned from "
    "their searches. Merge the new session signals into the existing summary and return the "
    "updated summary (a few sentences). If the existing summary was written by the user "
    "(user_edited=true), preserve their statements verbatim and only APPEND newly observed "
    "signals. Return ONLY the summary text, no prose wrapper, no markdown."
)


async def generate_clarify_questions(query: str, cv_text: str, preference_memory: str) -> list[str]:
    llm = large_llm()
    resp = await llm.ainvoke([
        SystemMessage(content=_CLARIFY_SYSTEM),
        HumanMessage(content=(
            f"Query:\n{query}\n\nCV (excerpt):\n{cv_text[:1500]}\n\n"
            f"Known preferences:\n{preference_memory or '(none)'}"
        )),
    ])
    try:
        data = _parse_json(resp.content)
        return [str(q) for q in data][:2] if isinstance(data, list) else []
    except Exception:
        _log.warning("[advanced.llm] clarify parse failed")
        return []


async def critique_results(query: str, hits: list[dict]) -> dict:
    llm = large_llm()
    titles = "\n".join(f"- {h['_source'].get('summary_text', '')[:200]}" for h in hits[:10])
    resp = await llm.ainvoke([
        SystemMessage(content=_CRITIQUE_SYSTEM),
        HumanMessage(content=f"Query:\n{query}\n\nTop results:\n{titles or '(none)'}"),
    ])
    try:
        data = _parse_json(resp.content)
        return {"need_refine": bool(data.get("need_refine")), "refined_query": data.get("refined_query")}
    except Exception:
        _log.warning("[advanced.llm] critique parse failed")
        return {"need_refine": False, "refined_query": None}


async def score_fit(cv_text: str, preference_memory: str, hits: list[dict]) -> list[dict]:
    llm = large_llm()
    jobs_blob = "\n".join(
        f'{{"job_id": "{h["_source"].get("job_id")}", "summary": "{h["_source"].get("summary_text", "")[:400]}"}}'
        for h in hits
    )
    resp = await llm.ainvoke([
        SystemMessage(content=_FIT_SYSTEM),
        HumanMessage(content=(
            f"CV (excerpt):\n{cv_text[:1500]}\n\nPreferences:\n{preference_memory or '(none)'}\n\n"
            f"Jobs:\n{jobs_blob}"
        )),
    ])
    fallback = [
        {"job_id": h["_source"].get("job_id"), "fit_score": 0, "fit_rationale": "", "fit_gaps": ""}
        for h in hits
    ]
    try:
        data = _parse_json(resp.content)
        return data if isinstance(data, list) else fallback
    except Exception:
        _log.warning("[advanced.llm] fit-score parse failed")
        return fallback


async def distill_memory(existing: str, user_edited: bool, query: str, filters_summary: str,
                         clarify_qa: list[tuple[str, str]]) -> str:
    llm = large_llm()
    qa = "\n".join(f"Q: {q} A: {a}" for q, a in clarify_qa) or "(none)"
    resp = await llm.ainvoke([
        SystemMessage(content=_DISTILL_SYSTEM),
        HumanMessage(content=(
            f"Existing summary (user_edited={str(user_edited).lower()}):\n{existing or '(none)'}\n\n"
            f"New session — query: {query}\nfilters: {filters_summary}\nclarifications:\n{qa}"
        )),
    ])
    return resp.content.strip()
