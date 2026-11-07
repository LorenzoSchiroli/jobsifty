import pytest

from backend.search.places import city_key, city_name, places, split_places


@pytest.mark.parametrize("location,expected", [
    ("Munich, Bavaria, Germany", (["munich"], ["Germany"])),
    ("München, BY, Germany", (["munich"], ["Germany"])),
    ("Greater Munich Metropolitan Area", (["munich"], ["Germany"])),  # a known city brings its country
    ("Berlin; Munich; Remote", (["berlin", "munich"], ["Germany"])),
    ("Deutschland", ([], ["Germany"])),
    ("London Area, United Kingdom", (["london"], ["United Kingdom"])),
    ("Ingolstadt, Bavaria, Germany", (["ingolstadt"], ["Germany"])),  # an unknown city keeps its own name
    ("Barcelona", (["barcelona"], ["Spain"])),
    ("San Francisco, CA", (["san francisco"], [])),  # CA is California here, not Canada
    ("Remote", ([], [])),
    (None, ([], [])),
])
def test_places_reads_cities_and_countries(location, expected):
    assert places(location) == expected


def test_city_names_and_keys():
    assert city_key("Milano") == city_key("Greater Milan Area") == "milan"
    assert city_name("warszawa") == "Warsaw" and city_name("Ingolstadt") == "Ingolstadt"


def test_split_places_tells_countries_from_cities():
    assert split_places(["Munich", "Schweiz", "Augsburg"]) == (["munich", "augsburg"], ["Switzerland"])
