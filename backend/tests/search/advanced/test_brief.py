import pytest
from pydantic import ValidationError

from backend.search.advanced.brief import (
    Brief,
    BriefItem,
    LockedRequirementError,
    hard_filters,
    merge_brief,
    relax,
    satisfies,
    violations,
)
from tests.search.advanced.samples import NO_CONSULTING, REMOTE, STARTUP


def test_dropping_a_locked_item_is_refused():
    with pytest.raises(LockedRequirementError, match="remote only"):
        merge_brief(Brief(items=[REMOTE]), Brief(items=[]), unlock_allowed=False)


def test_changing_a_locked_value_is_refused():
    hybrid = REMOTE.model_copy(update={"value": "hybrid"})
    with pytest.raises(LockedRequirementError):
        merge_brief(Brief(items=[REMOTE]), Brief(items=[hybrid]), unlock_allowed=False)


def test_unlock_allowed_accepts_the_change():
    assert merge_brief(Brief(items=[REMOTE]), Brief(items=[]), unlock_allowed=True).items == []


def test_inferred_items_change_freely():
    assert merge_brief(Brief(items=[STARTUP]), Brief(items=[]), unlock_allowed=False).items == []


def test_new_stated_items_are_allowed():
    assert merge_brief(Brief(), Brief(items=[REMOTE]), unlock_allowed=False).items == [REMOTE]


def test_relax_turns_stated_require_into_inferred_prefer():
    item = relax(Brief(items=[REMOTE]), ["Remote only"]).items[0]
    assert (item.source, item.kind) == ("inferred", "prefer")


def test_relax_keeps_avoid_as_avoid():
    item = relax(Brief(items=[NO_CONSULTING]), ["no consulting"]).items[0]
    assert (item.source, item.kind) == ("inferred", "avoid")


def test_hard_filters_map_stated_items_and_skip_inferred():
    f = hard_filters(Brief(items=[REMOTE, NO_CONSULTING, STARTUP]))
    assert (f.location_type, f.is_consulting, f.is_startup) == ("remote", False, None)
    assert f.max_age_hours is None and f.semantic_query == ""


def test_hard_filters_skip_the_lifted_item():
    assert hard_filters(Brief(items=[REMOTE]), lifted="remote only") is None


def test_hard_filters_map_country():
    item = BriefItem(text="in Germany", kind="require", source="stated", field="company_country", value="Germany")
    assert hard_filters(Brief(items=[item])).country == "Germany"


def test_violations_ignore_unknown_and_catch_contradictions():
    b = Brief(items=[REMOTE, NO_CONSULTING])
    assert violations(b, {"location_type": None, "is_consulting": False}) == []
    assert violations(b, {"location_type": "on-site", "is_consulting": True}) == ["remote only", "no consulting"]


def test_violations_languages_and_review_score():
    langs = BriefItem(text="english only", kind="require", source="stated", field="languages_required",
                      value=["english"])
    score = BriefItem(text="good reviews", kind="require", source="stated", field="min_review_score", value=4.0)
    b = Brief(items=[langs, score])
    assert violations(b, {"languages_required": ["english"], "min_review_score": 4.2}) == []
    assert violations(b, {"languages_required": ["english", "german"], "min_review_score": 3.1}) == [
        "english only", "good reviews"]


def test_avoid_with_non_bool_field_is_invalid():
    with pytest.raises(ValidationError):
        BriefItem(text="no leads", kind="avoid", source="stated", field="seniority", value="lead")


def test_field_and_value_must_be_set_together():
    with pytest.raises(ValidationError):
        BriefItem(text="x", kind="require", source="stated", field="seniority")


def test_to_api_groups_items():
    api = Brief(items=[REMOTE, NO_CONSULTING, STARTUP]).to_api()
    assert [i["text"] for i in api["stated"]] == ["remote only"]
    assert [i["text"] for i in api["inferred"]] == ["startup"]
    assert [i["text"] for i in api["avoid"]] == ["no consulting"]


def test_locked_item_flipped_to_inferred_is_refused():
    soft = REMOTE.model_copy(update={"source": "inferred"})
    with pytest.raises(LockedRequirementError):
        merge_brief(Brief(items=[REMOTE]), Brief(items=[soft]), unlock_allowed=False)


def test_locked_item_with_changed_kind_is_refused():
    inverted = NO_CONSULTING.model_copy(update={"kind": "require"})
    with pytest.raises(LockedRequirementError):
        merge_brief(Brief(items=[NO_CONSULTING]), Brief(items=[inverted]), unlock_allowed=False)


def test_reordered_list_value_is_not_a_change():
    a = BriefItem(text="langs", kind="require", source="stated", field="languages_required",
                  value=["english", "german"])
    b = a.model_copy(update={"value": ["german", "english"]})
    assert merge_brief(Brief(items=[a]), Brief(items=[b]), unlock_allowed=False).items == [b]


def test_value_types_are_checked_per_field():
    for field, value in [("location_type", True), ("is_startup", "yes"), ("min_review_score", "high"),
                         ("languages_required", [1]), ("seniority", ["senior", 3]), ("seniority", "boss"),
                         ("location_type", "senior"), ("languages_required", ["klingon"]),
                         ("employment_type", "gig")]:
        with pytest.raises(ValidationError):
            BriefItem(text="x", kind="require", source="stated", field=field, value=value)


def test_lone_language_string_is_wrapped_and_score_is_float():
    lang = BriefItem(text="en", kind="require", source="stated", field="languages_required", value="english")
    assert lang.value == ["english"]
    score = BriefItem(text="s", kind="require", source="stated", field="min_review_score", value=4)
    assert score.value == 4.0 and isinstance(score.value, float)


def test_preferably_remote_stays_tradeable():
    pref = BriefItem(text="preferably remote", kind="prefer", source="stated", field="location_type",
                     value="remote")
    assert pref.source == "inferred"  # a stated preference is coerced: preferences are never locked
    b = Brief(items=[pref])
    assert b.locked() == []
    assert hard_filters(b) is None
    assert violations(b, {"location_type": "on-site"}) == []
    assert merge_brief(b, Brief(items=[]), unlock_allowed=False).items == []


def test_satisfies_true_false_and_unknown():
    assert satisfies(REMOTE, {"location_type": "remote"}) is True
    assert satisfies(REMOTE, {"location_type": "onsite"}) is False
    assert satisfies(REMOTE, {"location_type": None}) is None
    assert satisfies(REMOTE, {}) is None


def test_satisfies_avoid_flips_the_bool():
    assert satisfies(NO_CONSULTING, {"is_consulting": False}) is True
    assert satisfies(NO_CONSULTING, {"is_consulting": True}) is False
    assert satisfies(NO_CONSULTING, {"is_consulting": None}) is None


# --- the stated posting window ------------------------------------------------------------------

def _window(days, source="stated"):
    return BriefItem(text="last 2 weeks", kind="require", source=source, field="posted_within_days", value=days)


def test_window_item_holds_whole_days():
    assert _window(14).value == 14 and isinstance(_window(14).value, int)
    assert _window(7.0).value == 7 and isinstance(_window(7.0).value, int)
    for bad in (0, 2.5, "two weeks", True):
        with pytest.raises(ValidationError, match="whole number of days"):
            _window(bad)


def test_stated_window_is_no_hard_filter_and_only_a_stated_one_counts():
    from backend.search.advanced.brief import stated_window

    assert hard_filters(Brief(items=[_window(14)])) is None
    assert hard_filters(Brief(items=[REMOTE, _window(14)])).location_type == "remote"
    assert stated_window(Brief(items=[REMOTE, _window(14)])).value == 14
    assert stated_window(Brief(items=[_window(14, source="inferred")])) is None


def _speaks(*languages):
    return BriefItem(text="languages from the CV", kind="require", source="inferred", field="languages_required",
                     value=list(languages))


def test_inferred_language_item_gives_a_gap_per_language_the_user_does_not_list():
    from backend.search.advanced.brief import inferred_shortfalls

    brief = Brief(items=[_speaks("italian", "english", "german")])
    assert inferred_shortfalls(brief, {"languages_required": ["spanish"]}) == (["requires Spanish"], [])
    assert inferred_shortfalls(brief, {"languages_required": ["english", "spanish", "french"]}) == (
        ["requires Spanish", "requires French"], [])
    assert inferred_shortfalls(brief, {"languages_required": ["english"]}) == ([], [])
    assert inferred_shortfalls(brief, {"languages_required": []}) == ([], [])  # unknown: no gap
    assert inferred_shortfalls(brief, {}) == ([], [])


def test_other_inferred_fields_are_trade_offs_and_stated_and_preferred_items_are_skipped():
    from backend.search.advanced.brief import inferred_shortfalls

    senior = BriefItem(text="mid or senior", kind="require", source="inferred", field="seniority",
                       value=["mid", "senior"])
    good = BriefItem(text="good reviews", kind="require", source="inferred", field="min_review_score", value=4)
    brief = Brief(items=[senior, good, STARTUP, REMOTE, _speaks("english")])  # STARTUP a preference, REMOTE stated
    fields = {"seniority": "lead", "min_review_score": 3.2, "is_startup": False, "location_type": "on-site",
              "languages_required": ["german"]}
    assert inferred_shortfalls(brief, fields) == (
        ["requires German"], ["lead level, not mid or senior", "reviews 3.2, below 4"])


def test_an_inferred_window_trade_off_counts_whole_days_up():
    from backend.search.advanced.brief import inferred_shortfalls

    brief = Brief(items=[_window(1, source="inferred")])
    assert inferred_shortfalls(brief, {"posted_within_days": 1.2}) == ([], ["posted 2 days ago"])  # not "1 days"
    assert inferred_shortfalls(brief, {"posted_within_days": 1.0}) == ([], [])
    assert inferred_shortfalls(Brief(items=[_window(3, source="inferred")]),
                               {"posted_within_days": 8.1}) == ([], ["posted 9 days ago"])


def test_window_is_met_by_the_jobs_age_in_days():
    assert satisfies(_window(7), {"posted_within_days": 6.5}) is True
    assert satisfies(_window(7), {"posted_within_days": 9.0}) is False
    assert satisfies(_window(7), {"posted_within_days": None}) is None
