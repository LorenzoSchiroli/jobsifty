import logging
import unicodedata

import requests

from ingestion.utils.text import clean_html

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def is_latin_script(text: str) -> bool:
    """True when every letter is Latin, accented ones included (é, ß, ł): European titles
    pass, Cyrillic, Greek, Arabic, CJK and other scripts do not. Digits, punctuation and
    symbols are ignored."""
    return all(
        unicodedata.name(c, "").startswith("LATIN") for c in text or "" if unicodedata.category(c).startswith("L")
    )


def _strip_html(html: str) -> str:
    return clean_html(html, aggressive=True)


def _fetch_description_from_url(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        return clean_html(resp.text, aggressive=True) or None
    except Exception as e:
        logger.debug("Failed to fetch description from %s: %s", url, e)
        return None
