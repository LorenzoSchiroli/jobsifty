import re

import ftfy
import trafilatura
from bs4 import BeautifulSoup

_HTML_TAG = re.compile(r"<[a-zA-Z!/][^>]*>")


def clean_html(html: str, aggressive: bool = True) -> str:
    """Extract and clean main text from HTML.

    aggressive=True  — strict main-content extraction; best for job descriptions stored to DB.
    aggressive=False — recall mode, preserves more content; best for company pages fed to an LLM.
    """
    if not html or not html.strip():
        return ""

    # Plain or markdown text (jobspy's Indeed/Glassdoor descriptions) is no HTML tree:
    # trafilatura would only log two errors before the fallback below takes it anyway.
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=not aggressive,
        no_fallback=False,
    ) if _HTML_TAG.search(html) else None

    if extracted:
        text = extracted
    else:
        # Fallback for short fragments or pages trafilatura can't identify a main block in
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)

    if not text:
        return ""

    text = ftfy.fix_text(text)
    text = re.sub(r"[-=*_]{3,}", " ", text)   # horizontal dividers: ---, ===, ***
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _dedup_paragraphs(text)
    return text.strip()


def has_text(html: str) -> bool:
    return bool(clean_html(html))


MAX_OFFER_DESCRIPTION_CHARS = 16_000
MAX_COMPANY_DESCRIPTION_CHARS = 64_000


def truncate_description(text: str) -> str:
    if len(text) <= MAX_OFFER_DESCRIPTION_CHARS:
        return text
    truncated = text[:MAX_OFFER_DESCRIPTION_CHARS]
    last_break = max(truncated.rfind("\n"), truncated.rfind(". "))
    return truncated[:last_break + 1].strip() if last_break > 0 else truncated.strip()


def _dedup_paragraphs(text: str) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for para in re.split(r"\n+", text):
        key = re.sub(r"\s+", " ", para.strip().lower())
        if key and key not in seen:
            seen.add(key)
            result.append(para.strip())
    return "\n\n".join(result)
