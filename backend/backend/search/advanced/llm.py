import os

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def get_agent_llm() -> BaseChatModel:
    """The advanced-search model: Groq by default, another OpenAI-compatible endpoint via ADVANCED_LLM_BASE_URL / _MODEL (it is sent GROQ_API_KEY).

    It reads the CV, so it must stay a private provider. The env is read on every call.
    """
    return ChatOpenAI(
        model=os.environ.get("ADVANCED_LLM_MODEL") or os.environ["GROQ_MODEL_LARGE"],
        api_key=os.environ["GROQ_API_KEY"],
        base_url=os.environ.get("ADVANCED_LLM_BASE_URL") or _GROQ_BASE_URL,
        max_retries=3,
        timeout=60,
        reasoning_effort="low",
    )


_DISTILL_SYSTEM = (
    "You maintain a short natural-language summary of a job seeker's preferences, learned from "
    "their searches. Merge the new session signals into the existing summary and return the "
    "updated summary (a few sentences). If the existing summary was written by the user "
    "(user_edited=true), preserve their statements verbatim and only APPEND newly observed "
    "signals. Return ONLY the summary text, no prose wrapper, no markdown."
)


async def distill_memory(existing: str, user_edited: bool, query: str, filters_summary: str,
                         clarify_qa: list[tuple[str, str]]) -> str:
    llm = get_agent_llm()
    qa = "\n".join(f"Q: {q} A: {a}" for q, a in clarify_qa) or "(none)"
    resp = await llm.ainvoke([
        SystemMessage(content=_DISTILL_SYSTEM),
        HumanMessage(content=(
            f"Existing summary (user_edited={str(user_edited).lower()}):\n{existing or '(none)'}\n\n"
            f"New session — query: {query}\nfilters: {filters_summary}\nclarifications:\n{qa}"
        )),
    ])
    return resp.content.strip()
