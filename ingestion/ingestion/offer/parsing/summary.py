"""A short LLM summary of what the job is, embedded as one extra chunk.

The rules and chunks work without it; with it, search ranks noticeably better (the
summary keeps only the role, the plain chunks also carry the company pitch, benefits
and the like). It is optional: when every provider fails, the job keeps its chunks.
"""
import json
import logging
import re

from llmpool.llm import LLMPool
from ingestion.offer.parsing.sections import clean_input
from ingestion.offer.scraping.models import JobOffer
from ingestion.utils.text import MAX_OFFER_DESCRIPTION_CHARS

logger = logging.getLogger(__name__)

# The same lists main's offer extractor asked for, so the summaries read the same.
_PROMPT = (
    "Summarize the job offer below. Return ONLY valid JSON with exactly these fields:\n"
    '{{"role_info": list, "requirements": list, "responsibilities": list, "domain": list}}\n\n'
    "Each field is a list of short strings (keywords or brief phrases, not full sentences):\n"
    "  role_info: key facts about the role itself (e.g. team size, reporting line, contract type hints).\n"
    "  requirements: required skills, technologies, qualifications, and years of experience.\n"
    "  responsibilities: main tasks and duties of the role.\n"
    "  domain: business or technical domains the role operates in (e.g. 'fintech', 'machine learning', "
    "'e-commerce'). Keep this short — 1 to 3 items.\n"
    "Use [] when nothing applies, never null. All strings must be lowercase.\n\n"
    "Job title: {title}\n"
    "Job description:\n{description}"
)
_FIELDS = ("role_info", "requirements", "responsibilities", "domain")
# A per-minute 429 pauses a provider for about a minute: waiting it out costs a minute,
# skipping would leave every job parsed meanwhile without a summary.
_MAX_WAIT = 90.0


def _parse(content: str) -> str:
    """The lists joined into one text; raises on an answer without any of them."""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    data = json.loads(content)
    if isinstance(data, list):  # some models wrap the object in a list
        data = data[0] if data and isinstance(data[0], dict) else {}
    parts = [str(item).strip() for field in _FIELDS for item in (data.get(field) or []) if str(item).strip()]
    if not parts:
        raise ValueError("empty summary")
    return " ".join(parts)


def summarize(offer: JobOffer, llm: LLMPool | None) -> str | None:
    """The summary text, or None without a description or a working provider."""
    description = clean_input(offer.description)
    if llm is None or not llm.configured() or llm.down() or not description.strip():
        return None
    prompt = _PROMPT.format(title=offer.title, description=description[:MAX_OFFER_DESCRIPTION_CHARS])
    return llm.complete(prompt, _parse, what="offer summary", wait_up_to=_MAX_WAIT)
