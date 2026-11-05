"""The values the job index holds for the fields the agent filters on, and their normalisation.

Ingestion writes them (`ingestion/offer/parsing/rules.py`, `lexicon.py`) into keyword fields with no
normaliser, so a value outside these lists matches nothing. The Literals put the vocabulary in the
JSON schema the model sees; `normalise_*` map what the model may still write ("onsite", "en",
"Full time", "remote|hybrid") onto it, and refuse anything else with the allowed values.
"""
import re
from typing import Literal, get_args

from backend.search.countries import canonical_country

LocationType = Literal["remote", "hybrid", "on-site"]
Seniority = Literal["junior", "mid", "senior", "staff", "lead", "principal", "director"]
EmploymentType = Literal["full-time", "part-time", "contract", "freelance", "internship", "stage"]
Language = Literal[
    "english", "german", "french", "italian", "spanish", "dutch", "portuguese", "swedish", "polish",
    "danish", "norwegian", "finnish", "czech", "greek", "romanian", "hungarian", "turkish", "arabic",
    "chinese", "japanese", "russian",
]

LANGUAGE_CODES: dict[str, str] = {
    "english": "en", "german": "de", "french": "fr", "italian": "it", "spanish": "es", "dutch": "nl",
    "portuguese": "pt", "swedish": "sv", "polish": "pl", "danish": "da", "norwegian": "no", "finnish": "fi",
    "czech": "cs", "greek": "el", "romanian": "ro", "hungarian": "hu", "turkish": "tr", "arabic": "ar",
    "chinese": "zh", "japanese": "ja", "russian": "ru",
}

CHOICE_FIELDS: dict[str, tuple[str, ...]] = {
    "location_type": get_args(LocationType),
    "seniority": get_args(Seniority),
    "employment_type": get_args(EmploymentType),
}


def _squash(value: str) -> str:
    return re.sub(r"[\s_\-.]+", "", value.casefold())


# Keys are squashed (casefolded, without spaces, dashes, underscores and dots).
_SYNONYMS: dict[str, dict[str, str]] = {
    "location_type": {"inoffice": "on-site", "office": "on-site", "inperson": "on-site", "onpremise": "on-site",
                      "fullyremote": "remote", "wfh": "remote", "workfromhome": "remote"},
    "seniority": {"midlevel": "mid", "medior": "mid", "intermediate": "mid", "sr": "senior", "jr": "junior",
                  "entrylevel": "junior", "graduate": "junior"},
    "employment_type": {"permanent": "full-time", "contractor": "contract", "temporary": "contract",
                        "freelancer": "freelance", "intern": "internship"},
}
_CHOICES: dict[str, dict[str, str]] = {
    field: {_squash(v): v for v in values} | _SYNONYMS[field] for field, values in CHOICE_FIELDS.items()
}
_LANGUAGES: dict[str, str] = (
    {name: name for name in get_args(Language)}
    | {code: name for name, code in LANGUAGE_CODES.items()}
    | {"nb": "norwegian", "nn": "norwegian", "mandarin": "chinese"}
)

_SPLIT = re.compile(r"\s*(?:\||,|/|\bor\b)\s*")


def _parts(value) -> list:
    """A string split on | , / and "or" (an OR of values), or the items of a list."""
    if isinstance(value, str):
        return [p for p in _SPLIT.split(value) if p.strip()]
    if isinstance(value, list):
        return value
    raise ValueError(f"needs a string or a list of strings, got {value!r}")


def _one(field: str, value, table: dict[str, str], allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} needs strings, got {value!r}")
    key = _squash(value) if field in CHOICE_FIELDS else value.strip().casefold()
    if key not in table:
        raise ValueError(f"{field} cannot be {value!r}; use one of: {', '.join(allowed)}")
    return table[key]


def normalise_choice(field: str, value) -> str | list[str]:
    """One index value, or a list of them meaning any of them; a one-value list becomes that value."""
    values = list(dict.fromkeys(_one(field, v, _CHOICES[field], CHOICE_FIELDS[field]) for v in _parts(value)))
    if not values:
        raise ValueError(f"{field} needs a value")
    return values[0] if len(values) == 1 else values


def normalise_languages(value) -> list[str]:
    """Lowercase English language names, as the index stores them; ISO codes are mapped to names."""
    values = list(dict.fromkeys(_one("languages_required", v, _LANGUAGES, get_args(Language)) for v in _parts(value)))
    if not values:
        raise ValueError("languages_required needs a value")
    return values


def normalise_country(value) -> str:
    """The canonical name of a known country or ISO code, else the stripped text."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"company_country needs a country name, got {value!r}")
    return canonical_country(value) or value.strip()
