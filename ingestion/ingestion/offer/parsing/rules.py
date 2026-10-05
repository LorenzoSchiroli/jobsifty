"""Deterministic extraction of structured offer fields (replaces the LLM extractor).

Each function is pure and looks at the title first, then the body text. Values
use the same vocabularies as ingestion.offer.models (Seniority, EmploymentType,
LocationType) and the backend's query parser, so filters keep matching.
"""
import re
from functools import lru_cache

from ingestion.offer.parsing.lexicon import (
    CURRENCY,
    EMPLOYMENT_TYPE,
    EMPLOYMENT_TYPE_BODY,
    LANGUAGE_NAMES,
    LANGUAGE_REQUIREMENT_PATTERNS,
    LOCATION_HYBRID,
    LOCATION_NOT_REMOTE,
    LOCATION_ONSITE,
    LOCATION_REMOTE,
    REMOTE_IN_LOCATION,
    SENIORITY_TITLE,
    YEARS_EXPERIENCE,
)

_I = re.IGNORECASE
_SENIORITY_RES = [(re.compile(p, _I), v) for p, v in SENIORITY_TITLE]
_YEARS_RES = [re.compile(p, _I) for p in YEARS_EXPERIENCE]
_EMPLOYMENT_RES = [(re.compile(p, _I), v) for p, v in EMPLOYMENT_TYPE]
_EMPLOYMENT_BODY_RES = [(re.compile(p, _I), v) for p, v in EMPLOYMENT_TYPE_BODY]
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUMBER_WORD_YEARS_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")\b(?:\s*\(\d{1,2}\))?(?=\s*\+?\s*(?:years?|yrs?)\b)", _I
)
_PAREN_NUMBER_RE = re.compile(r"\b\w+\s*\((\d{1,2})\)(?=\s*\+?\s*(?:years?|yrs?)\b)", _I)
_HYBRID_RE = re.compile(LOCATION_HYBRID, _I)
_REMOTE_RE = re.compile(LOCATION_REMOTE, _I)
_ONSITE_RE = re.compile(LOCATION_ONSITE, _I)
_NOT_REMOTE_RE = re.compile(LOCATION_NOT_REMOTE, _I)
_NEGATION_RE = re.compile(r"\b(?:not|no|non|nicht|kein\w*|pas|ni)\b[^.\n]*$", _I)
_REMOTE_IN_LOCATION_RE = re.compile(REMOTE_IN_LOCATION, _I)

_LANGUAGE_RES = {
    name: re.compile(
        "|".join(p.replace("{lang}", "|".join(re.escape(v) for v in variants)) for p in LANGUAGE_REQUIREMENT_PATTERNS),
        _I,
    )
    for name, variants in LANGUAGE_NAMES.items()
}

_LANGUAGE_MENTION_RES = {
    name: re.compile(r"\b(?:" + "|".join(re.escape(v) for v in variants) + r")\b", _I)
    for name, variants in LANGUAGE_NAMES.items()
}

_NUM = r"(?:\d{1,3}(?:[.,' ]\d{3})+|\d{1,9})(?:[.,]\d{1,2})?\s?[kK]?"
_PERIOD = (
    r"(?:\s*(?:per|/|a|an|p\.?|pro|par|al|por)\s*"
    r"(?:year|annum|yr|month|hour|h|jahr|monat|stunde|an|mois|heure|anno|mese|ora|año|mes|jaar|maand))?"
)
_NOT_AMOUNT = r"(?!\s*(?:million|mio|mln|bn|billion|m\b|b\b|%))"
_SALARY_RES = [
    re.compile(rf"{CURRENCY}\s?{_NUM}\s*(?:-|–|—|to|bis|à|a)\s*{CURRENCY}?\s?{_NUM}{_NOT_AMOUNT}(?:\s?{CURRENCY})?{_PERIOD}", _I),
    re.compile(rf"(?<![\w.,]){_NUM}\s*(?:-|–|—|to|bis|à|a)\s*{_NUM}\s?{CURRENCY}{_NOT_AMOUNT}{_PERIOD}", _I),
    re.compile(rf"{CURRENCY}\s?(?:\d{{1,3}}[.,' ]\d{{3}}(?:[.,]\d{{1,2}})?|\d{{2,3}}\s?[kK]\b){_NOT_AMOUNT}{_PERIOD}", _I),
    re.compile(rf"(?<![\w.,])(?:\d{{1,3}}[.,' ]\d{{3}}|\d{{2,3}}\s?[kK])\s?{CURRENCY}{_NOT_AMOUNT}{_PERIOD}", _I),
    # un-grouped amounts only with an explicit pay period: "1500€ / mese", "EUR 6000 gross"
    re.compile(rf"(?:{CURRENCY}\s?\d{{4,6}}|(?<![\w.,])\d{{4,6}}\s?{CURRENCY})(?:\s*(?:gross|net|brut|brutto|netto|lordo|lordi|bruto))?"
               rf"\s*(?:per|/|a|al|par|pro|p\.)\s*(?:year|annum|month|jahr|monat|an|mois|anno|mese|año|mes|jaar|maand|persona)\b", _I),
    # French daily rate: "TJM : 700"
    re.compile(r"\b(?:tjm|taux journalier)\b[^\d\n]{0,20}\d{3,4}\s?(?:€|eur)?", _I),
    # day rates: "£800 inside IR35", "€600/day"
    re.compile(rf"{CURRENCY}\s?\d{{3,4}}(?:\s*(?:-|–|to)\s*{CURRENCY}?\s?\d{{3,4}})?\s*(?:/|per|a)?\s*(?:day|daily|inside|outside)\b(?:\s+ir35)?", _I),
]

_LANGUAGE_DETECTION_CHARS = 3000


def seniority(title: str, text: str | None = None) -> str | None:
    """Title words first; otherwise the minimum years of experience asked for."""
    for regex, value in _SENIORITY_RES:
        if regex.search(title or ""):
            return value
    if not text:
        return None
    text = _NUMBER_WORD_YEARS_RE.sub(lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), text)
    text = _PAREN_NUMBER_RE.sub(r"\1", text)
    years = [int(m.group(1)) for regex in _YEARS_RES for m in regex.finditer(text) if m.group(1)]
    years = [y for y in years if y <= 20]
    if not years:
        return None
    y = min(years)
    if y <= 2:
        return "junior"
    if y < 5:
        return "mid"
    return "senior"


def _search_affirmed(regex: re.Pattern, text: str) -> re.Match | None:
    """First match not preceded by a negation ("This is not a junior or internship position")."""
    for m in regex.finditer(text):
        if not _NEGATION_RE.search(text[max(0, m.start() - 30):m.start()]):
            return m
    return None


def employment_type(title: str, text: str | None = None) -> str:
    for regex, value in _EMPLOYMENT_RES:
        if regex.search(title or ""):
            return value
    for regex, value in _EMPLOYMENT_BODY_RES:
        if _search_affirmed(regex, text or ""):
            return value
    return "full-time"


def location_type(title: str, location: str | None, text: str | None = None) -> str | None:
    head = f"{title or ''} {location or ''}"
    if _HYBRID_RE.search(head):
        return "hybrid"
    if _REMOTE_IN_LOCATION_RE.search(head) or _REMOTE_RE.search(title or ""):
        return "remote"
    body = text or ""
    if _NOT_REMOTE_RE.search(body):
        return "on-site"
    if _search_affirmed(_HYBRID_RE, body):
        return "hybrid"
    if _search_affirmed(_REMOTE_RE, body):
        return "remote"
    if _ONSITE_RE.search(body):
        return "on-site"
    if location and location.strip():
        return "on-site"
    return None


def office(location: str | None, location_type_value: str | None) -> str | None:
    if location_type_value not in ("on-site", "hybrid") or not location:
        return None
    value = location.strip().lower()
    if not value or _REMOTE_IN_LOCATION_RE.search(value):
        return None
    return value


def salary_range(text: str | None) -> str | None:
    if not text:
        return None
    for regex in _SALARY_RES:
        if m := regex.search(text):
            return re.sub(r"\s+", " ", m.group(0)).strip().lower()
    return None


@lru_cache(maxsize=1)
def _detector():
    from lingua import Language, LanguageDetectorBuilder

    languages = [
        Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.ITALIAN, Language.SPANISH,
        Language.DUTCH, Language.PORTUGUESE, Language.SWEDISH, Language.POLISH, Language.DANISH,
        Language.BOKMAL, Language.FINNISH, Language.CZECH, Language.ROMANIAN, Language.GREEK,
    ]
    return LanguageDetectorBuilder.from_languages(*languages).build()


def text_language(text: str | None) -> str | None:
    if not text or len(text.strip()) < 20:
        return None
    detected = _detector().detect_language_of(text[:_LANGUAGE_DETECTION_CHARS])
    if detected is None:
        return None
    name = detected.name.lower()
    return "norwegian" if name == "bokmal" else name


def languages_required(text: str | None, text_language_value: str | None = None) -> list[str]:
    # The language the offer is written in is implicitly required.
    found: list[str] = [text_language_value] if text_language_value else []
    if text:
        for name, regex in _LANGUAGE_RES.items():
            if name not in found and regex.search(text):
                found.append(name)
        # A requirement phrase usually lists several languages in one sentence
        # ("fließend Deutsch und Englisch", "inglés alto y español operativo").
        for line in re.split(r"[.\n;•]", text):
            if any(regex.search(line) for regex in _LANGUAGE_RES.values()):
                for name, mention in _LANGUAGE_MENTION_RES.items():
                    if name not in found and mention.search(line):
                        found.append(name)
    return found
