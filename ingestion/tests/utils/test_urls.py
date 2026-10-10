import pytest

from ingestion.utils.urls import registered_domain, site_root


@pytest.mark.parametrize("url, expected", [
    ("https://careers.criteo.com/jobs", "criteo.com"),
    ("https://zendesk.wd1.myworkdayjobs.com/en-US/zendesk", "myworkdayjobs.com"),
    ("https://www.bbc.co.uk/news", "bbc.co.uk"),
    ("not a url", ""),
])
def test_registered_domain(url, expected):
    assert registered_domain(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://careers.criteo.com/jobs", "https://www.criteo.com"),
    ("https://newsroom.accenture.com/x", "https://www.accenture.com"),
    ("https://www.huk.de/", "https://www.huk.de"),
    ("https://datawizards.io/about", "https://datawizards.io"),
    ("http://www.microsoft.com/", "http://www.microsoft.com"),
    ("https://www.example.co.uk:8443/a", "https://www.example.co.uk"),
    ("www.acme.com", None),
    ("", None),
    (None, None),
])
def test_site_root(url, expected):
    assert site_root(url) == expected
