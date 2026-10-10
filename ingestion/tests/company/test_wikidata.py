from unittest.mock import MagicMock, patch

import pytest

from ingestion.company.scraping import wikidata
from ingestion.company.scraping.wikidata import lookup, normalize


def _item(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}, "rank": "normal"}


def _time(t):
    return {"mainsnak": {"datavalue": {"value": {"time": t}}}, "rank": "normal"}


def _employees(amount, when):
    return {
        "mainsnak": {"datavalue": {"value": {"amount": amount}}},
        "rank": "normal",
        "qualifiers": {"P585": [{"datavalue": {"value": {"time": when}}}]},
    }


_SIEMENS = {
    "claims": {
        "P31": [_item("Q891723")],
        "P571": [_time("+1847-10-01T00:00:00Z")],
        "P1128": [_employees("+311000", "+2022-01-01T00:00:00Z"), _employees("+320000", "+2023-01-01T00:00:00Z")],
        "P17": [_item("Q183")],
        "P452": [_item("Q11650")],
    },
    "descriptions": {"en": {"value": "German multinational technology conglomerate"}},
}
_LABELS = {
    "Q183": {"labels": {"en": {"value": "Germany"}}},
    "Q11650": {"labels": {"en": {"value": "electronics"}}},
}


def _fake_api(search_results, entities):
    def fake_get(url, params, headers, timeout):
        resp = MagicMock()
        if params["action"] == "wbsearchentities":
            resp.json.return_value = {"search": search_results}
        elif "claims" in params["props"]:
            resp.json.return_value = {"entities": {i: entities[i] for i in params["ids"].split("|") if i in entities}}
        else:
            resp.json.return_value = {"entities": {i: _LABELS[i] for i in params["ids"].split("|") if i in _LABELS}}
        return resp
    return fake_get


@pytest.mark.parametrize("name, expected", [
    ("Siemens AG", "siemens"),
    ("Zühlke Group", "zuhlke"),
    ("Smith+Nephew", "smith nephew"),
    ("McKinsey & Company", "mckinsey and"),
    ("Group", "group"),
])
def test_normalize(name, expected):
    assert normalize(name) == expected


def test_lookup_returns_facts_for_matching_org():
    search = [{"id": "Q81230", "label": "Siemens", "match": {"text": "Siemens"}}]
    with patch.object(wikidata.requests, "get", side_effect=_fake_api(search, {"Q81230": _SIEMENS})):
        result = lookup("Siemens AG")

    assert result == {
        "founded_year": 1847,
        "employee_count": "320000",
        "country": "Germany",
        "industry": "electronics",
        "description": "German multinational technology conglomerate",
    }


def test_lookup_returns_the_official_website_as_site_root():
    entity = {**_SIEMENS, "claims": {**_SIEMENS["claims"], "P856": [
        {"mainsnak": {"datavalue": {"value": "https://new.siemens.com/global/en.html"}}, "rank": "normal"},
    ]}}
    search = [{"id": "Q81230", "label": "Siemens", "match": {"text": "Siemens"}}]
    with patch.object(wikidata.requests, "get", side_effect=_fake_api(search, {"Q81230": entity})):
        assert lookup("Siemens AG")["website"] == "https://www.siemens.com"


def test_lookup_rejects_label_mismatch():
    search = [{"id": "Q1", "label": "Siemens Healthineers", "match": {"text": "Siemens Healthineers"}}]
    with patch.object(wikidata.requests, "get", side_effect=_fake_api(search, {"Q1": _SIEMENS})):
        assert lookup("Siemens") == {}


def test_lookup_rejects_non_organisation():
    light = {"claims": {"P31": [_item("Q9128")]}, "descriptions": {"en": {"value": "electromagnetic radiation"}}}
    search = [{"id": "Q9128", "label": "Light", "match": {"text": "Light"}}]
    with patch.object(wikidata.requests, "get", side_effect=_fake_api(search, {"Q9128": light})):
        assert lookup("Light") == {}


def test_lookup_returns_empty_on_error():
    with patch.object(wikidata.requests, "get", side_effect=RuntimeError("network down")):
        assert lookup("Siemens") == {}
