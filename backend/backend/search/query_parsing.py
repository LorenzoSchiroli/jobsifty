import re

from backend.search.countries import COUNTRY_NAMES
from backend.search.filters import SearchFilters

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_UNIT_HOURS = {"hour": 1, "day": 24, "week": 168}

# (regex, field, value, strip?) — first match per field wins.
_ENUM_RULES = [
    (r"\bwork from home\b", "location_type", "remote", True),
    (r"\bwfh\b", "location_type", "remote", True),
    (r"\bremote\b", "location_type", "remote", True),
    (r"\bhybrid\b", "location_type", "hybrid", True),
    (r"\bon[- ]?site\b", "location_type", "on-site", True),
    (r"\bfull[- ]?time\b", "employment_type", "full-time", True),
    (r"\bpart[- ]?time\b", "employment_type", "part-time", True),
    (r"\binternships?\b", "employment_type", "internship", True),
    (r"\binterns?\b", "employment_type", "internship", False),
    (r"\bfreelance(?:r)?\b", "employment_type", "freelance", True),
    (r"\bcontractor\b", "employment_type", "contract", False),
    (r"\bcontract\b", "employment_type", "contract", False),
    (r"\bstage\b", "employment_type", "stage", False),
    (r"\bentry[- ]?level\b", "seniority", "junior", True),
    (r"\bjunior\b", "seniority", "junior", True),
    (r"\bjr\.?\b", "seniority", "junior", True),
    (r"\bmid[- ]?level\b", "seniority", "mid", True),
    (r"\bsenior\b", "seniority", "senior", True),
    (r"\bsr\.?\b", "seniority", "senior", True),
    (r"\blead\b", "seniority", "lead", False),
    (r"\bprincipal\b", "seniority", "principal", False),
    (r"\bstaff\b", "seniority", "staff", False),
    (r"\bdirector\b", "seniority", "director", False),
]

_STRICT_RULES = [
    (r"\bstrictly\b", True),
    (r"\bexactly\b", True),
    (r"\bno exceptions\b", True),
]

_LANGS = [
    "english", "german", "french", "spanish", "italian", "dutch",
    "portuguese", "chinese", "mandarin", "japanese", "arabic",
    "russian", "polish", "swedish",
]
_LANG_ALT = "|".join(_LANGS)

# longest first so multi-word countries win
_COUNTRY_KEYS = sorted(COUNTRY_NAMES, key=len, reverse=True)

_NEG = r"\b(?:(?:no|not|without|excluding)\s+|non[\s-]?)"
_OP = r"(?:>=|≥|above|over|at least|min(?:imum)?|greater than|more than)\s*"


def parse_query(query: str) -> SearchFilters:
    text = query.lower()
    semantic = text
    fields: dict = {}

    def strip(pattern: str) -> None:
        nonlocal semantic
        semantic = re.sub(pattern, " ", semantic)

    # --- negatable booleans ---
    for field, kw in (("is_startup", r"start[- ]?ups?"), ("is_consulting", r"consult(?:ing|ancy)")):
        if re.search(_NEG + kw + r"\b", text):
            fields[field] = False
            strip(_NEG + kw + r"\b")
        elif re.search(r"\b" + kw + r"\b", text):
            fields[field] = True
            strip(r"\b" + kw + r"\b")

    # --- time window ---
    num = r"(\d+|" + "|".join(_NUM_WORDS) + r")"
    m = re.search(r"\b(?:last|past|previous|within|in the last)\s+" + num + r"\s+(hour|day|week)s?\b", text)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        fields["max_age_hours"] = n * _UNIT_HOURS[m.group(2)]
        strip(m.re.pattern)
    elif re.search(r"\btoday\b", text):
        fields["max_age_hours"] = 24
        strip(r"\btoday\b")
    elif re.search(r"\byesterday\b", text):
        fields["max_age_hours"] = 48
        strip(r"\byesterday\b")
    elif re.search(r"\bthis week\b", text):
        fields["max_age_hours"] = 168
        strip(r"\bthis week\b")

    # --- numeric thresholds (explicit phrasing only) ---
    mf = re.search(r"financial(?: health)?(?: score)?\s*" + _OP + r"(\d+)", text)
    if mf:
        fields["min_financial_health_score"] = int(mf.group(1))
        strip(mf.re.pattern)
    mr = re.search(r"review(?: score)?\s*" + _OP + r"(\d+(?:\.\d+)?)", text)
    if mr:
        fields["min_review_score"] = float(mr.group(1))
        strip(mr.re.pattern)

    # --- languages (trigger-based) ---
    langs: list[str] = []
    for lang in re.findall(r"\b(" + _LANG_ALT + r")[- ]speaking\b", text):
        langs.append(lang)
    for chain in re.findall(
        r"\b(?:in|fluent in|speaks?|speaking|knowledge of)\s+((?:" + _LANG_ALT +
        r")(?:\s*(?:,|and|&)\s*(?:" + _LANG_ALT + r"))*)\b", text):
        langs.extend(re.findall(_LANG_ALT, chain))
    if langs:
        seen = []
        for lang in langs:
            title = lang.capitalize()
            if title not in seen:
                seen.append(title)
        fields["languages_required"] = seen
        strip(r"\b(" + _LANG_ALT + r")[- ]speaking\b")
        strip(r"\b(?:in|fluent in|speaks?|speaking|knowledge of)\s+((?:" + _LANG_ALT +
              r")(?:\s*(?:,|and|&)\s*(?:" + _LANG_ALT + r"))*)\b")

    # --- country ---
    # Keep country in semantic_query too — BM25 still benefits from the place name.
    for key in _COUNTRY_KEYS:
        if re.search(r"\b" + re.escape(key) + r"\b", text):
            fields["country"] = COUNTRY_NAMES[key]
            break

    # --- enum filters (first per field wins) ---
    for pattern, field, value, do_strip in _ENUM_RULES:
        if field in fields:
            continue
        if re.search(pattern, text):
            fields[field] = value
            if do_strip:
                strip(pattern)

    # --- strict flag ---
    for pattern, value in _STRICT_RULES:
        if "strict" in fields:
            continue
        if re.search(pattern, text):
            fields["strict"] = value
            strip(pattern)
            break

    semantic = re.sub(r"\s{2,}", " ", semantic).strip(" ,.-")
    if not semantic:
        semantic = query.strip()

    return SearchFilters(semantic_query=semantic, **fields)
