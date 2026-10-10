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
