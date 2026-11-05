import json
import re
import logging

from backend.llm import get_pool

_log = logging.getLogger(__name__)


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
    def parse(content: str) -> list[str]:
        data = _parse_json(content)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array")
        return [str(q) for q in data][:2]

    out = await get_pool().acomplete(
        f"Query:\n{query}\n\nCV (excerpt):\n{cv_text[:1500]}\n\n"
        f"Known preferences:\n{preference_memory or '(none)'}",
        parse, "clarify", system=_CLARIFY_SYSTEM, json=False,
    )
    if out is None:
        _log.warning("[advanced.llm] clarify failed")
        return []
    return out


# Each scored job is sent as title + company + the start of its cleaned text;
# ten jobs at this size keep a fit-scoring call around 5k tokens.
FIT_SCORED_JOBS = 10
FIT_TEXT_CHARS = 1500
CRITIQUE_TEXT_CHARS = 300


def job_text(hit: dict, limit: int) -> str:
    """Title, company and the start of the boilerplate-stripped description."""
    source = hit["_source"]
    head = source.get("title") or ""
    if source.get("company_name"):
        head += f" at {source['company_name']}"
    chunks = [c for c in source.get("chunks") or [] if not c.get("summary")]
    # The first text chunk is "title\n<start of the cleaned text>".
    body = chunks[0]["text"].split("\n", 1)[1] if chunks and "\n" in chunks[0]["text"] else ""
    return f"{head}\n{body}".strip()[:limit]


async def critique_results(query: str, hits: list[dict]) -> dict:
    def parse(content: str) -> dict:
        data = _parse_json(content)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return {"need_refine": bool(data.get("need_refine")), "refined_query": data.get("refined_query")}

    titles = "\n".join(f"- {job_text(h, CRITIQUE_TEXT_CHARS)}" for h in hits[:10])
    out = await get_pool().acomplete(
        f"Query:\n{query}\n\nTop results:\n{titles or '(none)'}",
        parse, "critique", system=_CRITIQUE_SYSTEM,
    )
    if out is None:
        _log.warning("[advanced.llm] critique failed")
        return {"need_refine": False, "refined_query": None}
    return out


async def score_fit(cv_text: str, preference_memory: str, hits: list[dict]) -> list[dict]:
    hits = hits[:FIT_SCORED_JOBS]
    scored_ids = {h["_source"].get("job_id") for h in hits}

    def parse(content: str) -> list[dict]:
        data = _parse_json(content)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array")
        out = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("job_id"), str):
                raise ValueError("expected objects with a string job_id")
            try:
                score = int(item["fit_score"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("fit_score is not a number") from None
            item = {**item, "fit_score": score}
            if item["job_id"] in scored_ids:  # an id the model made up is dropped
                out.append(item)
        return out

    jobs_blob = "\n".join(
        json.dumps({"job_id": h["_source"].get("job_id"), "job": job_text(h, FIT_TEXT_CHARS)}, ensure_ascii=False)
        for h in hits
    )
    out = await get_pool().acomplete(
        f"CV (excerpt):\n{cv_text[:1500]}\n\nPreferences:\n{preference_memory or '(none)'}\n\n"
        f"Jobs:\n{jobs_blob}",
        parse, "score_fit", system=_FIT_SYSTEM, json=False,
    )
    if out is None:
        _log.warning("[advanced.llm] fit-score failed")
        return [
            {"job_id": h["_source"].get("job_id"), "fit_score": 0, "fit_rationale": "", "fit_gaps": ""}
            for h in hits
        ]
    return out


async def distill_memory(existing: str, user_edited: bool, query: str, filters_summary: str,
                         clarify_qa: list[tuple[str, str]]) -> str:
    def parse(content: str) -> str:
        text = content.strip()
        if not text:  # an empty answer must not overwrite the stored memory
            raise ValueError("empty summary")
        return text

    qa = "\n".join(f"Q: {q} A: {a}" for q, a in clarify_qa) or "(none)"
    out = await get_pool().acomplete(
        f"Existing summary (user_edited={str(user_edited).lower()}):\n{existing or '(none)'}\n\n"
        f"New session — query: {query}\nfilters: {filters_summary}\nclarifications:\n{qa}",
        parse, "distill_memory", system=_DISTILL_SYSTEM, json=False,
    )
    return existing if out is None else out
