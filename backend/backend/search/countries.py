"""The one country table: canonical English names, ISO codes and aliases.

Ingestion stores company.country as a canonical English name (sometimes an ISO code); the aliases
mirror `_COUNTRIES` in `ingestion/ingestion/company/parsing/rules.py` (separate service, no import).
"""

# canonical name -> ISO 3166-1 alpha-2 code
_ISO: dict[str, str] = {
    "Germany": "DE", "France": "FR", "Italy": "IT", "Spain": "ES", "Portugal": "PT", "Netherlands": "NL",
    "Belgium": "BE", "Luxembourg": "LU", "Switzerland": "CH", "Austria": "AT", "United Kingdom": "GB",
    "Ireland": "IE", "Poland": "PL", "Czech Republic": "CZ", "Sweden": "SE", "Norway": "NO", "Denmark": "DK",
    "Finland": "FI", "Greece": "GR", "Hungary": "HU", "Romania": "RO", "Estonia": "EE", "Lithuania": "LT",
    "Latvia": "LV", "United States": "US", "Canada": "CA",
}

# other spellings -> canonical name
_ALIASES: dict[str, str] = {
    "deutschland": "Germany", "italia": "Italy", "españa": "Spain", "espana": "Spain",
    "the netherlands": "Netherlands", "nederland": "Netherlands", "belgië": "Belgium", "belgique": "Belgium",
    "schweiz": "Switzerland", "suisse": "Switzerland", "svizzera": "Switzerland",
    "österreich": "Austria", "osterreich": "Austria",
    "uk": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "great britain": "United Kingdom", "britain": "United Kingdom",
    "polska": "Poland", "czechia": "Czech Republic", "sverige": "Sweden", "norge": "Norway",
    "danmark": "Denmark", "suomi": "Finland", "magyarország": "Hungary",
    "usa": "United States", "united states of america": "United States", "america": "United States",
}

# casefolded canonical names and aliases (no ISO codes: they are ambiguous in free text) -> canonical name
COUNTRY_NAMES = {name.casefold(): name for name in _ISO} | _ALIASES
_BY_ISO = {iso: name for name, iso in _ISO.items()}

_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
    "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def canonical_country(value: str | None, *, in_location: bool = False) -> str | None:
    """A country name, alias or ISO code as the canonical name; None when it is none of them.

    In a free-text location a code that is also a US state ("San Francisco, CA") is not a country.
    """
    if not value:
        return None
    v = value.strip()
    if name := COUNTRY_NAMES.get(v.casefold()):
        return name
    if in_location and v.upper() in _US_STATES:
        return None
    return _BY_ISO.get(v.upper())


def country_values(value: str) -> list[str]:
    """The values company.country may hold for a country: its canonical name and ISO code (else the text)."""
    name = canonical_country(value)
    return [name, _ISO[name]] if name else [value]


def same_country(a: str | None, b: str | None) -> bool:
    """Whether two country names, aliases or ISO codes name the same country ("Germany" and "DE" do)."""
    if not a or not b:
        return False
    return (canonical_country(a) or a.strip()).casefold() == (canonical_country(b) or b.strip()).casefold()
