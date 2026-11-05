"""Rule-based removal of obvious personal data from CV text (Italian and English).

Regexes and word lists only, no I/O. Rules run in a fixed order: consent sentence,
email, link, id (IBAN, codice fiscale), labelled lines (birth date, address,
nationality / marital status / gender), unlabelled street address, phone, and last
the name. The name is found on the *original* top lines but replaced at the end, so
emails and URLs that contain it are already gone.

Principle: when a rule is unsure, it leaves the text alone. Skills, employers,
schools, job titles and dates are never touched; a missed item is for the user to
catch in the editor. Placeholders such as ``[EMAIL]`` are never matched or counted,
so running ``redact`` on its own output changes nothing.
"""

import re

PLACEHOLDER = re.compile(r"\[[A-Z ]+\]")

# kind -> placeholder
PLACEHOLDERS = {
    "consent": "[CONSENT]",
    "email": "[EMAIL]",
    "link": "[LINK]",
    "id": "[ID]",
    "birth_date": "[BIRTH DATE]",
    "address": "[ADDRESS]",
    "personal": "[REMOVED]",
    "phone": "[PHONE]",
    "name": "[NAME]",
    "surname": "[SURNAME]",
}

_FLAGS = re.IGNORECASE | re.MULTILINE


def _labelled(kind: str, labels: str, digit_without_colon: bool = False):
    """A line `Label: value`; only the value (to end of line) is replaced."""
    # Colon required, except for birth labels like "Nato il 12/03/1990" where a
    # digit right after the label makes the value unambiguous.
    sep = r"(?::[ \t]*" + (r"|[ \t]+(?=\d)" if digit_without_colon else "") + ")"
    return (kind, re.compile(rf"^[ \t]*(?:{labels})[ \t]*{sep}(?P<value>[^\n]*\S)", _FLAGS))


# Rules after consent, in order: (kind, pattern). A `value` group, if present, is
# the only part replaced; otherwise the whole match.
_RULES = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # Scheme / www URLs, and profile URLs of well-known sites (not bare domains such
    # as scikit-learn.org or Node.js). The last character may not be punctuation.
    ("link", re.compile(
        r"(?:https?://|www\.)[^\s|·,;<>()]*[^\s|·,;<>().]"
        r"|\b(?:[a-z]{2,3}\.)?(?:linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|twitter\.com|x\.com"
        r"|facebook\.com|instagram\.com|xing\.com|behance\.net|medium\.com|kaggle\.com)/[^\s|·,;<>()]*[^\s|·,;<>().]",
        re.IGNORECASE)),
    # IBAN: country + 2 check digits + at least 3 groups of 4, then a short tail.
    ("id", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b")),
    # Italian codice fiscale: 6 letters, 2 digits, month letter, 2 digits, letter, 3 digits, letter.
    ("id", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b")),
    _labelled("birth_date", r"Date of birth|Born|Data di nascita|Nato/a il|Nato il|Nata il|Nato|Nata", True),
    _labelled("address", r"Address|Indirizzo|Residenza"),
    _labelled("personal", r"Nationality|Nazionalità|Marital status|Stato civile|Gender|Sesso"),
    # Unlabelled street address at the start of a line, to end of line: Italian
    # "Via Roma 12, ..." (street word + capitalised name + number + comma); "Corso" is
    # also a course ("Corso Python 3, Udemy"), so it needs a 5-digit postcode after the comma ...
    ("address", re.compile(
        r"^[ \t]*(?:(?:Via|Viale|Piazza|Largo|Vicolo|Corso(?=[^\n]*,[ \t]*\d{5}))[ \t]+[A-ZÀ-Ý][^\n,\d]*?[ \t]+\d+[a-z]?[ \t]*,[^\n]*"
        # ... and English "12 Baker Street, ..." (number + capitalised words + street type + comma).
        r"|\d+[a-z]?[ \t]+[A-Z]\w+(?:[ \t]+[A-Z]\w+)*[ \t]+(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|Lane|Drive)[ \t]*,[^\n]*)",
        re.MULTILINE)),
    # Phone: international (+44 20 7946 0958), Italian mobile (333 123 4567) and
    # numbers with a leading 0 in groups (02 1234 5678). The lookbehind keeps it out of
    # the middle of other numbers, money (€300 000 000), dates and versions.
    ("phone", re.compile(
        r"(?<![\w.,/+€$£-])(?:"
        r"\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}"
        r"|3\d{2} ?\d{3} ?\d{3,4}"
        r"|0\d{1,3}[ ./-]?\d{3,4}[ ./-]?\d{3,4}"
        r")(?![\w]|[.,]\d)")),
]

_CONSENT_TRIGGERS = re.compile(
    r"trattamento dei dati personali|D\.Lgs\. 196/2003|GDPR 679/16|Regolamento UE 2016/679"
    r"|processing of my personal data",
    re.IGNORECASE)
# A sentence ends at . ! ? followed by a capital ("D.Lgs. 196/2003" does not end one).
_SENTENCE_END = re.compile(r"(?<=[.!?])(\s+)(?=[A-ZÀ-Ý])")

_NAME_WORD = re.compile(r"[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")
_NAME_CUT = re.compile(r"–| - |\||·|,")
_NAME_STOP = {w.lower() for w in (
    "Curriculum Vitae CV Resume Résumé Data Engineer Developer Scientist Analyst Senior Junior "
    "Lead Manager Consultant Experience Esperienza Profile Profilo Contact Contatti").split()}


def _is_placeholder(s: str) -> bool:
    return PLACEHOLDER.fullmatch(s.strip()) is not None


def _consent(text: str) -> tuple[str, int]:
    """Replace each sentence that carries a data-processing consent phrase."""
    count = 0
    lines = []
    for line in text.split("\n"):
        if _CONSENT_TRIGGERS.search(line):
            parts = _SENTENCE_END.split(line)  # sentences at even indexes, whitespace at odd
            for i in range(0, len(parts), 2):
                if _CONSENT_TRIGGERS.search(parts[i]):
                    parts[i] = PLACEHOLDERS["consent"]
                    count += 1
            line = "".join(parts)
        lines.append(line)
    return "\n".join(lines), count


def _find_name(lines: list[str]) -> list[str]:
    """First names then surname, from the first 3 non-empty lines; [] when unsure."""
    for line in [l.strip() for l in lines if l.strip()][:3]:
        if "[NAME]" in line or "[SURNAME]" in line:
            return []  # already redacted: do not pick another line as the name
        words = _NAME_CUT.split(line)[0].split()
        if 2 <= len(words) <= 4 and all(
                _NAME_WORD.fullmatch(w) and w.lower() not in _NAME_STOP for w in words):
            return words
    return []


def _replace(text: str, pattern: re.Pattern, kind: str) -> tuple[str, int]:
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        whole = m.group(0)
        if _is_placeholder(whole) or (
                "value" in pattern.groupindex and _is_placeholder(m.group("value"))):
            return whole
        count += 1
        if "value" in pattern.groupindex:
            start, end = m.span("value")
            return whole[: start - m.start()] + PLACEHOLDERS[kind] + whole[end - m.start():]
        return PLACEHOLDERS[kind]

    return pattern.sub(sub, text), count


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return the text with personal data replaced, and counts by kind (> 0 only)."""
    found: dict[str, int] = {}

    def add(kind: str, n: int) -> None:
        if n:
            found[kind] = found.get(kind, 0) + n

    names = _find_name(text.split("\n"))  # on the original text, replaced last
    out, n = _consent(text)
    add("consent", n)
    for kind, pattern in _RULES:
        out, n = _replace(out, pattern, kind)
        add(kind, n)

    # Name: whole-word occurrences as written, UPPER and Title (case-sensitive), so
    # "rosa" in prose stays. Words of 2 letters or fewer are ignored.
    kinds = {}
    for i, word in enumerate(names):
        kind = "surname" if i == len(names) - 1 else "name"
        if len(word) > 2:
            for form in (word, word.upper(), word.title()):
                kinds.setdefault(form, kind)
    if kinds:
        forms = sorted(kinds, key=len, reverse=True)
        pattern = re.compile(r"(?<![\w\[])(?:" + "|".join(map(re.escape, forms)) + r")(?![\w\]])")

        def sub(m: re.Match) -> str:
            add(kinds[m.group(0)], 1)
            return PLACEHOLDERS[kinds[m.group(0)]]

        out = pattern.sub(sub, out)
    return out, found
