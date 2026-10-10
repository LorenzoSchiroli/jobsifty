import re
from datetime import date

_SCORE_PATTERNS = [
    re.compile(r"\b([1-5][.,]\d)\s*(?:out of|/|of)\s*5\b", re.IGNORECASE),
    re.compile(r"\brated\s+([1-5][.,]\d)\b", re.IGNORECASE),
    re.compile(r"\b([1-5][.,]\d)\s*(?:★|stars?\b)", re.IGNORECASE),
    re.compile(r"★\s*([1-5][.,]\d)\b"),
]
_COUNT_PATTERN = re.compile(r"(\d[\d.,]*)\s*([kK])?\+?\s*(?:(?:company|employee|glassdoor)\s+)?reviews\b", re.IGNORECASE)

_CONSULTING_PATTERN = re.compile(
    r"\b(?:consulting|consultancy|consultants?|unternehmensberatung|beratung|conseil|consulenza|"
    r"consultor[ií]a|advisory)\b",
    re.IGNORECASE,
)

_STARTUP_MAX_AGE_YEARS = 10
_STARTUP_MAX_EMPLOYEES = 250

# country names (English + common local spellings) and ISO codes → canonical English name
_COUNTRIES: dict[str, str] = {
    "germany": "Germany", "deutschland": "Germany", "de": "Germany",
    "france": "France", "fr": "France",
    "italy": "Italy", "italia": "Italy", "it": "Italy",
    "spain": "Spain", "españa": "Spain", "espana": "Spain", "es": "Spain",
    "portugal": "Portugal", "pt": "Portugal",
    "netherlands": "Netherlands", "the netherlands": "Netherlands", "nederland": "Netherlands", "nl": "Netherlands",
    "belgium": "Belgium", "belgië": "Belgium", "belgique": "Belgium", "be": "Belgium",
    "luxembourg": "Luxembourg", "lu": "Luxembourg",
    "switzerland": "Switzerland", "schweiz": "Switzerland", "suisse": "Switzerland", "svizzera": "Switzerland",
    "ch": "Switzerland",
    "austria": "Austria", "österreich": "Austria", "osterreich": "Austria", "at": "Austria",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "gb": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "ireland": "Ireland", "ie": "Ireland",
    "poland": "Poland", "polska": "Poland", "pl": "Poland",
    "czech republic": "Czech Republic", "czechia": "Czech Republic", "cz": "Czech Republic",
    "sweden": "Sweden", "sverige": "Sweden", "se": "Sweden",
    "norway": "Norway", "norge": "Norway", "no": "Norway",
    "denmark": "Denmark", "danmark": "Denmark", "dk": "Denmark",
    "finland": "Finland", "suomi": "Finland", "fi": "Finland",
    "greece": "Greece", "gr": "Greece",
    "hungary": "Hungary", "magyarország": "Hungary", "hu": "Hungary",
    "romania": "Romania", "ro": "Romania",
    "estonia": "Estonia", "ee": "Estonia",
    "lithuania": "Lithuania", "lt": "Lithuania",
    "latvia": "Latvia", "lv": "Latvia",
    "united states": "United States", "usa": "United States", "us": "United States",
    "canada": "Canada",
}


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def _parse_count(digits: str, k: str | None) -> int | None:
    if k:
        try:
            return int(_to_float(digits) * 1000)
        except ValueError:
            return None
    cleaned = re.sub(r"[.,]", "", digits)
    return int(cleaned) if cleaned.isdigit() else None


def parse_glassdoor(snippets: list[str] | None) -> dict:
    """review_score / review_count from Glassdoor search snippets (first match wins)."""
    result: dict = {}
    for snippet in snippets or []:
        if "review_score" not in result:
            for pattern in _SCORE_PATTERNS:
                if m := pattern.search(snippet):
                    score = _to_float(m.group(1))
                    if 1.0 <= score <= 5.0:
                        result["review_score"] = score
                        break
        if "review_count" not in result and (m := _COUNT_PATTERN.search(snippet)):
            if (count := _parse_count(m.group(1), m.group(2))) is not None:
                result["review_count"] = count
        if len(result) == 2:
            break
    return result


def detect_consulting(*texts: str | None) -> bool | None:
    """True when any text names consulting/advisory work; None otherwise (never False)."""
    return True if any(t and _CONSULTING_PATTERN.search(t) for t in texts) else None


def parse_employee_count(value: str | int | None) -> int | None:
    """Upper bound of an employee count like '51-200', '1,234', '10K+'."""
    if value is None:
        return None
    numbers = [
        _parse_count(digits, k)
        for digits, k in re.findall(r"(\d[\d.,]*)\s*([kK])?", str(value))
    ]
    numbers = [n for n in numbers if n is not None]
    return max(numbers) if numbers else None


def detect_startup(founded_year: int | None, employee_count: str | int | None, today: date | None = None) -> bool | None:
    """True for young, small companies; None when the evidence is missing or points elsewhere."""
    employees = parse_employee_count(employee_count)
    if founded_year is None or employees is None:
        return None
    year = (today or date.today()).year
    if year - founded_year <= _STARTUP_MAX_AGE_YEARS and employees < _STARTUP_MAX_EMPLOYEES:
        return True
    return None


def country_from_location(location: str | None) -> str | None:
    """Country from a job location such as 'Berlin, Germany' or 'Munich, BY, DE'."""
    if not location:
        return None
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return None
    last = parts[-1].lower()
    # two-letter codes are only trusted with ≥3 parts ('City, ST' is usually a US state)
    if len(last) == 2 and len(parts) < 3:
        return None
    return _COUNTRIES.get(last)


_FOUNDED_PATTERN = re.compile(
    r"\b(?:founded|established|incorporated)\b[^.]{0,40}?\b(1[89]\d\d|20\d\d)\b", re.IGNORECASE,
)
_EMPLOYEES_PATTERN = re.compile(
    r"\b(\d{1,3}(?:[,.]\d{3})+|\d+)\+?\s*(?:employees|staff members|members of staff)\b", re.IGNORECASE,
)
_HQ_PATTERN = re.compile(r"\b(?:headquartered|based)\s+in\s+([^.;()]{2,60})")


def parse_answer(answer: str | None, today: date | None = None) -> dict:
    """founded_year / employee_count / country from a search engine's short answer about the company.

    Used when the LLM is unavailable. Only the answer is read, never the raw snippets:
    snippets mention other years and numbers (a 2025 award, an unrelated founding date).
    """
    if not answer:
        return {}
    result: dict = {}
    year = (today or date.today()).year
    if (m := _FOUNDED_PATTERN.search(answer)) and int(m.group(1)) <= year:
        result["founded_year"] = int(m.group(1))
    for m in _EMPLOYEES_PATTERN.finditer(answer):
        digits = m.group(1)
        # "in 2014 employees grew..." — a bare year is not a head count
        if re.fullmatch(r"(?:18|19|20)\d\d", digits):
            continue
        result["employee_count"] = digits
        break
    if m := _HQ_PATTERN.search(answer):
        place = re.split(r"\s+(?:with|and|that|which|is|it|since|founded|specializ\w*)\b", m.group(1))[0]
        if country := country_from_location(place) or _COUNTRIES.get(place.strip(" ,").lower()):
            result["country"] = country
    return result
