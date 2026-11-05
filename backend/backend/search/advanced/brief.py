"""The agent's model of what the user wants.

Stated requirements are locked: only the user may relax them. Inferred ones the
agent may trade. Pure data and rules, no I/O.

A mapped item's value is in the index vocabulary (`vocabulary.py`): it is normalised when the item
is built, so a value the index does not hold can never become a locked requirement.
"""
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
    same_country,
)
from backend.search.filters import SearchFilters

# A company-size requirement stays possible as a text-only item: employee_count is not in the index.
BriefField = Literal[
    "location_type",
    "seniority",
    "employment_type",
    "languages_required",
    "is_consulting",
    "is_startup",
    "company_country",
    "min_review_score",
]

_BOOL_FIELDS = ("is_consulting", "is_startup")

_VALUE_HELP = (
    "With `field` only. location_type / seniority / employment_type: one value, or a list meaning any of "
    "them (\"remote or hybrid\" is [\"remote\", \"hybrid\"]); languages_required: a list of language names; "
    "is_consulting / is_startup: true or false; company_country: a country name; min_review_score: 1-5."
)


def normalise_value(field: str, value):
    """The value of `field` in the index vocabulary; ValueError when the field cannot take it."""
    if field in CHOICE_FIELDS:
        return normalise_choice(field, value)
    if field == "languages_required":
        return normalise_languages(value)
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
    return value  # an unknown field: the Literal on `field` refuses it


class BriefItem(BaseModel):
    text: str = Field(max_length=80)
    kind: Literal["require", "prefer", "avoid"]
    source: Literal["stated", "inferred"]
    field: BriefField | None = None
    value: (
        LocationType | list[LocationType] | Seniority | list[Seniority] | EmploymentType | list[EmploymentType]
        | list[Language] | bool | float | str | None
    ) = Field(default=None, description=_VALUE_HELP)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data):
        if isinstance(data, dict) and data.get("field") is not None and data.get("value") is not None:
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
    attr = "country" if item.field == "company_country" else item.field
    value = item.value
    if item.kind == "avoid" and isinstance(value, bool):
        value = not value
    return attr, value


def _stated_mapped(brief: Brief) -> list[BriefItem]:
    # A stated "prefer" stays locked in the brief but is neither a hard filter nor a violation.
    return [i for i in brief.locked() if i.field is not None and i.kind != "prefer"]


def hard_filters(brief: Brief, *, lifted: str | None = None) -> SearchFilters | None:
    lifted_key = lifted.casefold() if lifted is not None else None
    values: dict = {}
    for item in _stated_mapped(brief):
        if lifted_key is not None and item.text.casefold() == lifted_key:
            continue
        attr, value = _wanted(item)
        values[attr] = value
    if not values:
        return None
    # The age window is applied by the soft filters; a hard set adds no second one.
    return SearchFilters(semantic_query="", max_age_hours=None, **values)


def satisfies(item: BriefItem, fields: dict) -> bool | None:
    """Whether the job's fields positively meet the item; None when the value is unknown.

    A list value means any of its values. An empty language list is unknown, as the hard filter
    treats it (it matches no `terms`).
    """
    attr, wanted = _wanted(item)
    actual = fields.get(item.field)
    if actual is None and item.field == "company_country":
        actual = fields.get("country")
    if actual is None or wanted is None or actual == []:
        return None
    if attr == "languages_required":
        allowed = {str(w).casefold() for w in wanted}
        return all(str(a).casefold() in allowed for a in actual)
    if attr == "min_review_score":
        return float(actual) >= float(wanted)
    if attr == "country":
        return same_country(str(actual), str(wanted))
    if isinstance(wanted, (str, list)) and isinstance(actual, str):
        options = [wanted] if isinstance(wanted, str) else wanted
        return actual.casefold() in {o.casefold() for o in options}
    return actual == wanted


def violations(brief: Brief, fields: dict) -> list[str]:
    """Stated hard items the job's fields contradict (unknown values are not counted)."""
    return [i.text for i in _stated_mapped(brief) if satisfies(i, fields) is False]


def unmet(brief: Brief, fields: dict) -> list[str]:
    """Stated hard items the job does not positively meet, among the fields `fields` carries.

    Unknown counts as unmet, as the hard filter does: a job found before an item was stated is
    held to it like every job found after.
    """
    return [i.text for i in _stated_mapped(brief) if i.field in fields and satisfies(i, fields) is not True]
