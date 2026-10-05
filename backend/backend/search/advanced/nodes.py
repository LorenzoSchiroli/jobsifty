from langgraph.types import interrupt

from backend.search.query_understanding import extract_filters
from backend.search.retrieval import hybrid_retrieve
from backend.search.reranker import rerank
from backend.search.models_lifecycle import encode_query
from backend.search.advanced.llm import (
    generate_clarify_questions, critique_results, score_fit,
)


async def node_generate_questions(state: dict) -> dict:
    questions = await generate_clarify_questions(
        state["query"], state.get("cv_text", ""), state.get("preference_memory", "")
    )
    return {"clarify_questions": questions}


async def node_clarify(state: dict) -> dict:
    answers = interrupt({"clarify_questions": state.get("clarify_questions", [])})
    return {"clarify_answers": answers}


async def node_search(state: dict, *, biencoder, reranker, os_client, groq_client) -> dict:
    answers = state.get("clarify_answers") or []
    base_query = state.get("refined_query") or state["query"]
    augmented = (
        f"{base_query}\n"
        f"Clarifications: {' | '.join(str(a) for a in answers)}\n"
        f"Preferences: {state.get('preference_memory', '')}"
    )
    filters = await extract_filters(groq_client, state.get("cv_text", ""), augmented)
    embedding = encode_query(biencoder, filters.semantic_query)
    hits = await hybrid_retrieve(os_client, embedding, filters)
    ranked = rerank(reranker, hits, filters.semantic_query, embedding)
    return {"hits": ranked}


async def node_critique(state: dict) -> dict:
    if state.get("refined_once"):
        return {"need_refine": False}
    verdict = await critique_results(state["query"], state.get("hits", []))
    if verdict.get("need_refine"):
        return {"need_refine": True, "refined_once": True, "refined_query": verdict.get("refined_query")}
    return {"need_refine": False}


def _route_after_critique(state: dict) -> str:
    return "search" if state.get("need_refine") else "fit_score"


async def node_fit_score(state: dict) -> dict:
    scored = await score_fit(state.get("cv_text", ""), state.get("preference_memory", ""), state.get("hits", []))
    scored.sort(key=lambda r: r.get("fit_score", 0), reverse=True)
    return {"results": scored}
