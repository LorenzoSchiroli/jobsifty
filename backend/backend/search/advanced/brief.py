"""The agent's model of what the user wants.

Stated requirements are locked: only the user may relax them. Inferred ones the
agent may trade. Pure data and rules, no I/O.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.search.filters import SearchFilters

BriefField = Literal[
    "location_type",
    "seniority",
    "employment_type",
    "languages_required",
    "is_consulting",
    "is_startup",
    "company_country",
    "employee_count",
    "min_review_score",
]

_BOOL_FIELDS = ("is_consulting", "is_startup")


class BriefItem(BaseModel):
    text: str = Field(max_length=80)
    kind: Literal["require", "prefer", "avoid"]
    source: Literal["stated", "inferred"]
    field: BriefField | None = None
    value: str | bool | float | list[str] | None = None

    @model_validator(mode="after")
    def _check(self):
        if (self.field is None) != (self.value is None):
            raise ValueError("field and value must be set together")
        self._check_value_type()
        if self.kind == "avoid" and self.field is not None and self.field not in _BOOL_FIELDS:
            raise ValueError("avoid with a field is only allowed for is_consulting / is_startup")
        return self


    def _check_value_type(self) -> None:
        if self.field is None:
            return
        v = self.value
        if self.field == "languages_required":
            if isinstance(v, str):
                self.value = [v]
            elif not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                raise ValueError("languages_required needs a list of strings")
        elif self.field in _BOOL_FIELDS:
            if not isinstance(v, bool):
                raise ValueError(f"{self.field} needs a bool")
        elif self.field == "min_review_score":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("min_review_score needs a number")
            self.value = float(v)
        elif not isinstance(v, str):
            raise ValueError(f"{self.field} needs a string")


class Brief(BaseModel):
    items: list[BriefItem] = []
    notes: str = ""

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
    pass


def _norm(value):
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
                raise LockedRequirementError(f"locked requirement changed or dropped: {locked.text}")
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
    """Whether the job's fields positively meet the item; None when the value is unknown."""
    attr, wanted = _wanted(item)
    actual = fields.get(item.field)
    if actual is None and item.field == "company_country":
        actual = fields.get("country")
    if actual is None or wanted is None:
        return None
    if attr == "languages_required":
        allowed = {str(w).casefold() for w in ([wanted] if isinstance(wanted, str) else wanted)}
        return all(str(a).casefold() in allowed for a in actual)
    if attr == "min_review_score":
        return float(actual) >= float(wanted)
    if isinstance(wanted, str) and isinstance(actual, str):
        return actual.casefold() == wanted.casefold()
    return actual == wanted


def _contradicts(item: BriefItem, fields: dict) -> bool:
    return satisfies(item, fields) is False


def violations(brief: Brief, fields: dict) -> list[str]:
    return [i.text for i in _stated_mapped(brief) if _contradicts(i, fields)]
