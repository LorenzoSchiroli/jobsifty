from unittest.mock import patch

from ingestion.company.scraping.searcher import _financial_query, search_company_urls, search_financial


def test_search_returns_website_and_glassdoor_snippets():
    website_hits = [{"href": "https://acme.com", "title": "Acme Corp", "body": ""}]
    glassdoor_hits = [
        {"href": "https://www.glassdoor.com/Overview/acme", "title": "Acme", "body": "Great culture. 4.2 stars based on 35 reviews."},
    ]

    def mock_search(query, max_results):
        if "glassdoor" in query:
            return glassdoor_hits
        return website_hits

    with patch("ingestion.company.scraping.searcher._search", side_effect=mock_search):
        urls, snippets, financial_snippets = search_company_urls("Acme", "Berlin")

    assert urls["website"] == "https://acme.com"
    assert "glassdoor" not in urls
    assert any("4.2" in s for s in snippets)


def test_search_omits_missing_sources():
    with patch("ingestion.company.scraping.searcher._search", return_value=[]):
        urls, snippets, financial_snippets = search_company_urls("Unknown Corp", "Nowhere")

    assert urls == {}
    assert snippets == []
    assert financial_snippets == []


def test_search_handles_search_exception_gracefully():
    glassdoor_hits = [
        {"href": "https://www.glassdoor.com/Overview/acme", "title": "Acme", "body": "Nice culture. 4.1 stars."},
    ]

    def mock_search(query, max_results):
        if "glassdoor" in query:
            return glassdoor_hits
        raise Exception("rate limited")

    with patch("ingestion.company.scraping.searcher._search", side_effect=mock_search):
        urls, snippets, financial_snippets = search_company_urls("Acme", "Berlin")

    assert "website" not in urls
    assert any("4.1" in s for s in snippets)


def test_search_returns_partial_when_glassdoor_empty():
    website_hits = [{"href": "https://acme.com", "title": "Acme", "body": ""}]

    def mock_search(query, max_results):
        if "glassdoor" in query:
            return []
        if "financial health" in query:
            return []
        return website_hits

    with patch("ingestion.company.scraping.searcher._search", side_effect=mock_search):
        urls, snippets, financial_snippets = search_company_urls("Acme", "Berlin")

    assert urls == {"website": "https://acme.com"}
    assert snippets == []


def test_financial_query_uses_registration_number_when_provided():
    query = _financial_query("Acme", " Berlin", {"VAT": "DE123456789"})
    assert query == "DE123456789 financial"


def test_financial_query_falls_back_to_baseline_without_registration_numbers():
    query = _financial_query("Acme", " Berlin", None)
    assert query.startswith("Acme Berlin")
    assert "financial health" in query


def test_search_financial_uses_registration_number_in_query():
    hits = [{"href": "https://bundesanzeiger.de/acme", "body": "Revenue €50M."}]
    with patch("ingestion.company.scraping.searcher._search", return_value=hits) as mock_search:
        url, snippets = search_financial("Acme", "Berlin", {"VAT": "DE123456789"})

    query_used = mock_search.call_args[0][0]
    assert "DE123456789" in query_used
    assert url == "https://bundesanzeiger.de/acme"
    assert "Revenue €50M." in snippets


def test_search_financial_falls_back_to_baseline_without_registration_numbers():
    with patch("ingestion.company.scraping.searcher._search", return_value=[]) as mock_search:
        search_financial("Acme", "Berlin")

    query_used = mock_search.call_args[0][0]
    assert "financial health" in query_used


def test_search_returns_financial_snippets():
    website_hits = [{"href": "https://acme.com", "title": "Acme", "body": ""}]
    financial_hits = [
        {"href": "https://stockanalysis.com/stocks/acme", "title": "Acme Financials", "body": "Revenue grew 12% YoY. Strong cash position."},
    ]

    def mock_search(query, max_results):
        if "financial health" in query:
            return financial_hits
        if "glassdoor" in query:
            return []
        return website_hits

    with patch("ingestion.company.scraping.searcher._search", side_effect=mock_search):
        urls, glassdoor_snippets, financial_snippets = search_company_urls("Acme", "Berlin")

    assert urls["website"] == "https://acme.com"
    assert urls["financial"] == "https://stockanalysis.com/stocks/acme"
    assert any("12%" in s for s in financial_snippets)
    assert glassdoor_snippets == []
