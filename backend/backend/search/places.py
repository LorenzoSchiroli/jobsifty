"""Where a job is: the cities and countries of its free-text location, as the index stores them.

A location reads "Munich, Bavaria, Germany", "München, BY, Germany", "Greater Munich Metropolitan
Area", "Berlin; Munich; Remote" or just "Deutschland". The index keeps the job's cities (casefolded
English names: "munich") and countries (canonical names: "Germany"), so a search for Munich finds
München too, and a search for Germany finds a job whose location names only a known German city.
"""
import re

from backend.search.countries import canonical_country

# English city name -> (country, other spellings). The cities job boards list most; any other city
# is kept by its own casefolded name, so it is still found when spelled the way the board spells it.
_CITIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "London": ("United Kingdom", ()), "Manchester": ("United Kingdom", ()), "Birmingham": ("United Kingdom", ()),
    "Edinburgh": ("United Kingdom", ()), "Glasgow": ("United Kingdom", ()), "Cambridge": ("United Kingdom", ()),
    "Oxford": ("United Kingdom", ()), "Bristol": ("United Kingdom", ()), "Leeds": ("United Kingdom", ()),
    "Belfast": ("United Kingdom", ()), "Paris": ("France", ()), "Lyon": ("France", ()),
    "Toulouse": ("France", ()), "Lille": ("France", ()), "Bordeaux": ("France", ()), "Nantes": ("France", ()),
    "Marseille": ("France", ()), "Grenoble": ("France", ()), "Madrid": ("Spain", ()),
    "Barcelona": ("Spain", ()), "Valencia": ("Spain", ()), "Malaga": ("Spain", ("málaga",)),
    "Berlin": ("Germany", ()), "Munich": ("Germany", ("münchen", "muenchen")), "Hamburg": ("Germany", ()),
    "Frankfurt": ("Germany", ("frankfurt am main",)), "Cologne": ("Germany", ("köln", "koeln")),
    "Stuttgart": ("Germany", ()), "Dusseldorf": ("Germany", ("düsseldorf", "duesseldorf")),
    "Leipzig": ("Germany", ()), "Dresden": ("Germany", ()), "Hanover": ("Germany", ("hannover",)),
    "Karlsruhe": ("Germany", ()), "Nuremberg": ("Germany", ("nürnberg", "nuernberg")),
    "Amsterdam": ("Netherlands", ()), "Rotterdam": ("Netherlands", ()), "Utrecht": ("Netherlands", ()),
    "Eindhoven": ("Netherlands", ()), "The Hague": ("Netherlands", ("den haag",)),
    "Brussels": ("Belgium", ("bruxelles", "brussel")), "Antwerp": ("Belgium", ("antwerpen",)),
    "Ghent": ("Belgium", ("gent",)), "Leuven": ("Belgium", ()), "Zurich": ("Switzerland", ("zürich",)),
    "Geneva": ("Switzerland", ("genève", "geneve", "genf")), "Basel": ("Switzerland", ()),
    "Vienna": ("Austria", ("wien",)), "Milan": ("Italy", ("milano",)), "Rome": ("Italy", ("roma",)),
    "Turin": ("Italy", ("torino",)), "Bologna": ("Italy", ()), "Naples": ("Italy", ("napoli",)),
    "Florence": ("Italy", ("firenze",)), "Lisbon": ("Portugal", ("lisboa",)), "Porto": ("Portugal", ()),
    "Dublin": ("Ireland", ()), "Cork": ("Ireland", ()), "Galway": ("Ireland", ()),
    "Warsaw": ("Poland", ("warszawa",)), "Krakow": ("Poland", ("kraków", "cracow")),
    "Wroclaw": ("Poland", ("wrocław",)), "Prague": ("Czech Republic", ("praha",)), "Brno": ("Czech Republic", ()),
    "Stockholm": ("Sweden", ()), "Gothenburg": ("Sweden", ("göteborg",)), "Oslo": ("Norway", ()),
    "Copenhagen": ("Denmark", ("københavn",)), "Helsinki": ("Finland", ()), "Budapest": ("Hungary", ()),
    "Bucharest": ("Romania", ("bucurești", "bucuresti")), "Athens": ("Greece", ()),
    "Luxembourg": ("Luxembourg", ()),
}
CITY_NAMES: dict[str, str] = {
    key: name for name, (_, aliases) in _CITIES.items() for key in (name.casefold(), *aliases)
}
_COUNTRY_OF = {name.casefold(): country for name, (country, _) in _CITIES.items()}

_NOT_A_PLACE = {"remote", "hybrid", "anywhere", "worldwide", "europe", "emea", "eu"}
_AREA = re.compile(r"^(greater |city of )|( metropolitan area| metro area| area| region)$")


def city_key(text: str) -> str:
    """A city as the index stores it: its English name, casefolded ("München" and "Greater Munich
    Metropolitan Area" are "munich"); an unknown city is its own casefolded name."""
    key = text.strip().casefold()
    while (shorter := _AREA.sub("", key).strip()) != key:
        key = shorter
    return CITY_NAMES.get(key, key).casefold()


def city_name(text: str) -> str:
    """A city as the page shows it: the English name of a known one, else the text as written."""
    return CITY_NAMES.get(city_key(text), text.strip())


def places(location: str | None) -> tuple[list[str], list[str]]:
    """The cities (city_key) and countries (canonical names) a job location names.

    Places are split on ";" and "|"; in each, the last part is a country when it reads as one,
    and the first part is the city unless it is the country itself. A known city adds its country.
    """
    cities: dict[str, None] = {}
    countries: dict[str, None] = {}
    for place in re.split(r"[;|]", location or ""):
        parts = [p.strip() for p in place.split(",") if p.strip()]
        if not parts or parts[0].casefold() in _NOT_A_PLACE:
            continue
        if country := canonical_country(parts[-1], in_location=True):
            countries[country] = None
        if canonical_country(parts[0], in_location=True) is None:
            city = city_key(parts[0])
            cities[city] = None
            if known := _COUNTRY_OF.get(city):
                countries[known] = None
    return list(cities), list(countries)


def split_places(values: list[str]) -> tuple[list[str], list[str]]:
    """Places the user names, as (city keys, canonical countries): a country name is a country."""
    cities = [city_key(v) for v in values if canonical_country(v) is None]
    countries = [c for v in values if (c := canonical_country(v))]
    return cities, countries
