from unittest.mock import MagicMock, patch

from ingestion.company.company import enrich
from ingestion.company.models import CompanyExtraction, CompanyProfile


def _make_client():
    return MagicMock()


def test_enrich_returns_company_profile():
    info = CompanyExtraction(industry="Software", is_consulting=False, is_startup=True, country="DE", founded_year=2010)

    with patch("ingestion.company.company.scrape", return_value=({}, [])):
        with patch("ingestion.company.company.parse", return_value=info):
            result, _ = enrich("Acme", "Berlin", _make_client())

    assert isinstance(result, CompanyProfile)
    assert result.name == "Acme"
    assert result.country == "DE"
    assert result.founded_year == 2010
    assert result.industry == "Software"
    assert result.is_consulting is False
    assert result.is_startup is True


def test_enrich_handles_no_data_gracefully():
    with patch("ingestion.company.company.scrape", return_value=({}, [])):
        with patch("ingestion.company.company.parse", return_value=CompanyExtraction()):
            result, _ = enrich("Acme", "Berlin", _make_client())

    assert isinstance(result, CompanyProfile)
    assert result.name == "Acme"
    assert result.country is None


def test_enrich_passes_known_facts_to_scrape():
    known = {"website": "https://acme.com", "industry": "Software"}
    with patch("ingestion.company.company.scrape", return_value=({}, [])) as mock_scrape:
        with patch("ingestion.company.company.parse", return_value=CompanyExtraction()):
            enrich("Acme", "Berlin", None, known)

    mock_scrape.assert_called_once_with("Acme", "Berlin", known)


def _scrape_with(known: dict, wikidata: dict, searched: str | None) -> dict:
    from ingestion.company.scraping import scraping
    from ingestion.company.scraping.searcher import CompanySearch

    with patch.object(scraping, "wikidata_lookup", return_value=wikidata), \
         patch.object(scraping, "search_company", return_value=CompanySearch(website=searched)), \
         patch.object(scraping, "fetch_html", return_value=None):
        return scraping.scrape("Acme", "Berlin", known)[0]


def test_website_comes_from_known_then_search_then_wikidata():
    wikidata = {"website": "https://www.huk.de", "founded_year": 1933}
    assert _scrape_with({"website": "https://www.huk.de/x"}, wikidata, "https://x.com")["website_url"] == "https://www.huk.de/x"
    assert _scrape_with({}, wikidata, "https://huk.de")["website_url"] == "https://huk.de"
    result = _scrape_with({}, wikidata, None)  # the search found no site whose domain has the name
    assert result["website_url"] == "https://www.huk.de" and result["wikidata"]["founded_year"] == 1933


def test_wikidata_namesake_is_dropped_when_websites_disagree():
    chemicals = {"website": "http://www.synthesia.eu", "industry": "chemical industry", "founded_year": 1920}
    result = _scrape_with({}, chemicals, "https://www.synthesia.io")
    assert result["website_url"] == "https://www.synthesia.io"
    assert result["wikidata"] == {}
