"""The agent's model of what the user wants.

Stated requirements are locked: only the user may relax them. Inferred ones the
agent may trade. Pure data and rules, no I/O.

A mapped item's value is in the index vocabulary (`vocabulary.py`): it is normalised when the item
is built, so a value the index does not hold can never become a locked requirement.
"""
import math
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.search.advanced.vocabulary import (
    CHOICE_FIELDS,
    EmploymentType,
    Language,
    LocationType,
    Seniority,
    normalise_choice,
    normalise_country,
    normalise_languages,
    normalise_places,
)
from backend.search.countries import canonical_country, same_country
from backend.search.places import city_name, split_places
from backend.search.filters import SearchFilters, describe

# A company-size requirement stays possible as a text-only item: employee_count is not in the index.
BriefField = Literal[
    "location_type",
    "seniority",
    "employment_type",
    "languages_required",
    "is_consulting",
    "is_startup",
    "place",
    "company_country",
    "min_review_score",
    "posted_within_days",
]

# The posting window the user states, in days. Not an index filter: a stated one is the max_age_hours
# of every search (tools.search_jobs), and a job meets it when its age is within it.
WINDOW_FIELD = "posted_within_days"

_BOOL_FIELDS = ("is_consulting", "is_startup")

_VALUE_HELP = (
    "With `field` only. location_type / seniority / employment_type: one value, or a list meaning any of "
    "them (\"remote or hybrid\" is [\"remote\", \"hybrid\"]); languages_required: a list of language names; "
    "is_consulting / is_startup: true or false; place: where the job is, a list of city and country names "
    "(no regions) meaning any of them ([\"Munich\"], [\"Munich\", \"Austria\", \"Switzerland\"]); company_country: where the "
    "company is based, a country name (not where the job is); min_review_score: 1-5; "
    "posted_within_days: the days of a posting window the user, the preference memory or the CV states "
    "(\"last 2 weeks\" 14, \"this week\" 7, "
    "\"today\" 1)."
)


def normalise_value(field: str, value):
    """The value of `field` in the index vocabulary; ValueError when the field cannot take it."""
    if field in CHOICE_FIELDS:
        return normalise_choice(field, value)
    if field == "languages_required":
        return normalise_languages(value)
    if field == "place":
        return normalise_places(value)
    if field in _BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{field} needs true or false")
        return value
    if field == "min_review_score":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("min_review_score needs a number")
        return float(value)
    if field == "company_country":
        return normalise_country(value)
    if field == WINDOW_FIELD:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1 or value != int(value):
            raise ValueError(f"{WINDOW_FIELD} needs a whole number of days, at least 1")
        return int(value)
    return value  # an unknown field: the Literal on `field` refuses it


class BriefItem(BaseModel):
    text: str = Field(max_length=80)
    kind: Literal["require", "prefer", "avoid"]
    source: Literal["stated", "inferred"]
    field: BriefField | None = None
    value: (
        LocationType | list[LocationType] | Seniority | list[Seniority] | EmploymentType | list[EmploymentType]
        | list[Language] | list[str] | bool | int | float | str | None
    ) = Field(default=None, description=_VALUE_HELP)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        if isinstance(data, dict):
            if data.get("kind") == "prefer":
                data = {**data, "source": "inferred"}  # a preference is tradeable, so never locked
            if data.get("field") is not None and data.get("value") is not None:
                data = {**data, "value": normalise_value(data["field"], data["value"])}
        return data

    @model_validator(mode="after")
    def _check(self):
        if (self.field is None) != (self.value is None):
            raise ValueError("field and value must be set together")
        if self.kind == "avoid" and self.field is not None and self.field not in _BOOL_FIELDS:
            raise ValueError("avoid with a field is only allowed for is_consulting / is_startup")
        return self


class Brief(BaseModel):
    items: list[BriefItem] = []
    notes: str = ""

    @model_validator(mode="after")
    def _one_stated_item_per_field(self):
        seen: set[str] = set()
        for i in self.items:
            if i.source != "stated" or i.field is None:
                continue
            if i.field in seen:
                raise ValueError(f"two stated items set {i.field}; give one item whose value is a list "
                                 "of every value the user accepts")
            seen.add(i.field)
        return self

    def locked(self) -> list[BriefItem]:
        return [i for i in self.items if i.source == "stated"]

    def to_api(self) -> dict:
        return {
            "stated": [i.model_dump() for i in self.items if i.kind != "avoid" and i.source == "stated"],
            "inferred": [i.model_dump() for i in self.items if i.kind != "avoid" and i.source == "inferred"],
            "avoid": [i.model_dump() for i in self.items if i.kind == "avoid"],
            "notes": self.notes,
        }


class LockedRequirementError(ValueError):
    def __init__(self, item: BriefItem):
        super().__init__(f"locked requirement changed or dropped: {item.text}")
        self.item = item


MAX_ITEM_WORDS = 8


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def bundled(item: BriefItem, request: str) -> bool:
    """An item holding several requirements: a list (comma or semicolon), more than MAX_ITEM_WORDS
    words, or the whole request of more than two words. A brief item is one requirement, in the
    filters panel as in the filters, so "junior remote NLP engineer in Munich" is four items."""
    words = _words(item.text)
    return (any(ch in item.text for ch in ",;") or len(words) > MAX_ITEM_WORDS
            or (len(_words(request)) > 2 and words == _words(request)))


def _norm(value):
    if isinstance(value, str) and value:
        return [value]
    return sorted(value) if isinstance(value, list) else value


def _same(a: BriefItem, b: BriefItem) -> bool:
    """b keeps the locked item a: still stated, same kind, same meaning."""
    if b.source != "stated" or a.kind != b.kind:
        return False
    if a.field is not None:
        return a.field == b.field and _norm(a.value) == _norm(b.value)
    return b.field is None and a.text.casefold() == b.text.casefold()


def merge_brief(current: Brief, proposed: Brief, *, unlock_allowed: bool) -> Brief:
    if not unlock_allowed:
        for locked in current.locked():
            if not any(_same(locked, p) for p in proposed.items):
                raise LockedRequirementError(locked)
    return proposed


def relax(brief: Brief, texts: list[str]) -> Brief:
    wanted = {t.casefold() for t in texts}
    items = []
    for i in brief.items:
        if i.source == "stated" and i.text.casefold() in wanted:
            i = i.model_copy(
                update={"source": "inferred", "kind": "prefer" if i.kind == "require" else i.kind}
            )
        items.append(i)
    return brief.model_copy(update={"items": items})


def _wanted(item: BriefItem) -> tuple[str, object]:
    """The SearchFilters attribute and value the item asks for (avoid flips a bool)."""
    attr = {"company_country": "country", "place": "places"}.get(item.field, item.field)
    value = item.value
    if item.kind == "avoid" and isinstance(value, bool):
        value = not value
    return attr, value


def _stated_mapped(brief: Brief) -> list[BriefItem]:
    return [i for i in brief.locked() if i.field is not None]


def stated_window(brief: Brief) -> BriefItem | None:
    """The stated posting-window item, if the user stated one (at most one: one stated item per field)."""
    return next((i for i in _stated_mapped(brief) if i.field == WINDOW_FIELD), None)


def inferred_window(brief: Brief) -> BriefItem | None:
    """The posting window the preference memory or the CV states, recorded as an inferred item: the
    run's starting window, a preference that is neither a filter nor an eligibility rule."""
    return next((i for i in brief.items if i.source == "inferred" and i.field == WINDOW_FIELD), None)


def hard_filters(brief: Brief, *, loosened: dict | None = None) -> SearchFilters | None:
    """The stated items with a field, as the hard filters of a search. `loosened` maps a field to
    the value a loosened search uses instead (already normalised), or to None to drop the filter."""
    loosened = loosened or {}
    values: dict = {}
    for item in _stated_mapped(brief):
        if item.field == WINDOW_FIELD:
            continue
        if item.field in loosened:
            if loosened[item.field] is None:
                continue
            item = item.model_copy(update={"value": loosened[item.field]})
        attr, value = _wanted(item)
        values[attr] = value
    if not values:
        return None
    # The age window (a stated one included) is the variant's max_age_hours; a hard set adds no second one.
    return SearchFilters(semantic_query="", max_age_hours=None, **values)


def filter_label(item: BriefItem) -> str:
    """How the page lists the item among the active filters: a stated item with a field is a hard
    filter, worded as basic search words it ("remote", "no consulting": filters.describe); any other
    item is the agent's own text. (An avoid item with a field is a yes/no one, which _wanted flips.)"""
    if item.source == "stated" and item.field is not None and item.field != WINDOW_FIELD:
        attr, value = _wanted(item)
        if labels := describe(SearchFilters(semantic_query="", max_age_hours=None, **{attr: value})).preferred:
            return labels[0]
    return f"avoid: {item.text}" if item.kind == "avoid" else item.text


def _actual(item: BriefItem, fields: dict):
    actual = fields.get(item.field)
    if actual is None and item.field == "company_country":
        actual = fields.get("country")
    return actual


def _languages_beyond(required: list, spoken: list) -> list[str]:
    """The languages a job requires that are not among `spoken`."""
    allowed = {str(w).casefold() for w in spoken}
    return [str(a) for a in required if str(a).casefold() not in allowed]


def satisfies(item: BriefItem, fields: dict) -> bool | None:
    """Whether the job's fields positively meet the item; None when the value is unknown.

    A list value means any of its values. An empty language list is unknown, as the hard filter
    treats it (it matches no `terms`). The posting window compares the job's age in days.
    """
    attr, wanted = _wanted(item)
    actual = _actual(item, fields)
    if actual is None or wanted is None or actual == []:
        return None
    if attr == "languages_required":
        return not _languages_beyond(actual, wanted)
    if attr == "min_review_score":
        return float(actual) >= float(wanted)
    if attr == WINDOW_FIELD:
        return float(actual) <= float(wanted)
    if attr == "country":
        return same_country(str(actual), str(wanted))
    if attr == "places":  # the job's {"cities", "countries"}: met when it is in any wanted place
        cities, countries = split_places(wanted)
        return bool(set(actual["cities"]) & set(cities) or set(actual["countries"]) & set(countries))
    if isinstance(wanted, (str, list)) and isinstance(actual, str):
        options = [wanted] if isinstance(wanted, str) else wanted
        return actual.casefold() in {o.casefold() for o in options}
    return actual == wanted


def violations(brief: Brief, fields: dict) -> list[str]:
    """Stated hard items the job's fields contradict (unknown values are not counted)."""
    return [i.text for i in _stated_mapped(brief) if satisfies(i, fields) is False]


def misses(brief: Brief, fields: dict) -> list[str]:
    """The stated items a job does not positively meet, worded as the filters panel words them
    (filter_label): the hard items among the fields `fields` carries, "(unknown)" added when the
    job does not say.

    Unknown counts as missed, as the hard filter does: a job found before an item was stated, or
    by a loosened search, is held to it like every other job. Empty: the job meets every stated
    item code can check.
    """
    out = []
    for i in brief.locked():
        met = satisfies(i, fields) if i.field is not None and i.field in fields else True
        if met is not True:
            out.append(filter_label(i) + (" (unknown)" if met is None else ""))
    return out


def _any(value) -> str:
    return " or ".join(value) if isinstance(value, list) else str(value)


def _shortfall(item: BriefItem, fields: dict) -> list[str]:
    """How a job that does not meet `item` falls short, as short phrases; [] when it meets it or is unknown."""
    if satisfies(item, fields) is not False:
        return []
    _, wanted = _wanted(item)
    actual = _actual(item, fields)
    if item.field == "languages_required":
        return [f"requires {lang.capitalize()}" for lang in _languages_beyond(actual, wanted)]
    if item.field == "is_consulting":
        return ["a consultancy" if actual else "not a consultancy"]
    if item.field == "is_startup":
        return ["a startup" if actual else "not a startup"]
    if item.field == "company_country":
        return [f"company based in {canonical_country(str(actual)) or actual}, not {wanted}"]
    if item.field == "place":
        where = city_name(actual["cities"][0]).title() if actual["cities"] else actual["countries"][0]
        return [f"in {where}, not {_any(wanted)}"]
    if item.field == "min_review_score":
        return [f"reviews {float(actual):.1f}, below {float(wanted):g}"]
    if item.field == WINDOW_FIELD:
        days = math.ceil(actual)
        return [f"posted {days} day{'' if days == 1 else 's'} ago"]
    level = " level" if item.field == "seniority" else ""
    return [f"{actual}{level}, not {_any(wanted)}"]


def missing_languages(brief: Brief, fields: dict) -> list[str]:
    """The languages the job requires that the user's inferred languages item does not list
    (the gaps "requires Spanish" are worded from these); [] without such an item or when unknown."""
    missing: dict[str, None] = {}
    for i in brief.items:
        if (i.source, i.kind, i.field) == ("inferred", "require", "languages_required") \
                and satisfies(i, fields) is False:
            missing.update(dict.fromkeys(_languages_beyond(_actual(i, fields), i.value)))
    return list(missing)


def inferred_shortfalls(brief: Brief, fields: dict) -> tuple[list[str], list[str]]:
    """What the job's fields contradict among the inferred require items, as (gaps, trade_offs).

    Inferred items are no hard filters, so a job may break one. A language the user does not list
    is a gap (it requires Spanish, the CV lists Italian, English and German): the user cannot meet
    it. Any other field is a preference given up, a trade-off ("on-site, not remote"). Unknown
    values are neither.
    """
    gaps: dict[str, None] = {}
    trade_offs: dict[str, None] = {}
    for i in brief.items:
        if i.source == "inferred" and i.kind == "require" and i.field is not None:
            target = gaps if i.field == "languages_required" else trade_offs
            target.update(dict.fromkeys(_shortfall(i, fields)))
    return list(gaps), list(trade_offs)
