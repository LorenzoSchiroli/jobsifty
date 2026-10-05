import json
from unittest.mock import MagicMock

import httpx
import pytest
from groq import PermissionDeniedError, RateLimitError

from ingestion.company.parsing.extractor import breaker_open, extract_jsonld, extract_with_llm, reset_breaker

_HTML_WITH_JSONLD = """<html><head>
<script type="application/ld+json">
{
  "@type": "Organization",
  "url": "https://acme.com",
  "description": "Acme makes things.",
  "foundingDate": "2010",
  "numberOfEmployees": {"value": "51-200"},
  "aggregateRating": {"ratingValue": "4.2", "reviewCount": "312"},
  "address": {"addressCountry": "DE"}
}
</script>
</head><body></body></html>"""

_HTML_NO_JSONLD = "<html><body>We are Acme, founded in 2010 in Berlin.</body></html>"


def test_extract_jsonld_parses_all_fields():
    result = extract_jsonld(_HTML_WITH_JSONLD)
    assert result["website"] == "https://acme.com"
    assert result["review_score"] == 4.2
    assert result["review_count"] == 312
    assert result["country"] == "DE"
    assert result["founded_year"] == 2010
    assert result["employee_count"] == "51-200"
    assert result["description"] == "Acme makes things."


def test_extract_jsonld_returns_empty_on_no_jsonld():
    result = extract_jsonld(_HTML_NO_JSONLD)
    assert result == {}


def test_extract_jsonld_returns_empty_on_wrong_type():
    html = """<html><head><script type="application/ld+json">
    {"@type": "WebPage", "name": "Home"}
    </script></head></html>"""
    result = extract_jsonld(html)
    assert result == {}


_SOURCES = {"company_pages": ["We are Acme, founded in 2010 in Berlin."]}


@pytest.fixture(autouse=True)
def _reset_breaker():
    reset_breaker()
    yield
    reset_breaker()


def _client_returning(content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value.choices[0].message.content = content
    return client


def _status_error(cls, status: int, message: str):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com"))
    return cls(message, response=response, body=None)


def test_extract_with_llm_returns_parsed_fields():
    client = _client_returning(json.dumps({
        "website": "https://acme.com",
        "country": "Germany",
        "founded_year": 2010,
        "employee_count": "51-200",
        "industry": "Software",
        "is_consulting": False,
        "is_startup": True,
        "review_score": 4.2,
        "review_count": 312,
        "financial_health_score": 4,
        "financial_health_rationale": "Revenue grew 12% YoY.",
        "registration_numbers": {"VAT": "DE123456789", "EIN": "12-3456789"},
    }))

    result = extract_with_llm("Acme", "Berlin", client, _SOURCES)

    assert result.country == "Germany"
    assert result.is_consulting is False
    assert result.is_startup is True
    assert result.founded_year == 2010
    assert result.financial_health_score == 4
    assert result.registration_numbers == {"VAT": "DE123456789", "EIN": "12-3456789"}


def test_extract_with_llm_returns_none_on_invalid_json():
    assert extract_with_llm("Acme", "Berlin", _client_returning("not json at all"), _SOURCES) is None


def test_extract_with_llm_returns_none_on_api_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("boom")
    assert extract_with_llm("Acme", "Berlin", client, _SOURCES) is None
    assert not breaker_open()


def test_extract_with_llm_returns_none_without_client():
    assert extract_with_llm("Acme", "Berlin", None, _SOURCES) is None


def test_extract_with_llm_strips_markdown_code_block():
    client = _client_returning('```JSON\n{"country": "Germany"}\n```')
    assert extract_with_llm("Acme", "Berlin", client, _SOURCES).country == "Germany"


def test_extract_with_llm_includes_sources_in_prompt():
    client = _client_returning(json.dumps({}))
    extract_with_llm("Acme", "Berlin", client, {"financial_snippets": ["Revenue grew 12% YoY."]})
    prompt_sent = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "Revenue grew 12% YoY." in prompt_sent
    assert "financial_snippets" in prompt_sent


def test_breaker_trips_on_daily_quota_and_skips_later_calls():
    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(
        RateLimitError, 429, "Rate limit reached on tokens per day (TPD): Limit 200000",
    )
    assert extract_with_llm("Acme", "Berlin", client, _SOURCES) is None
    assert breaker_open()

    assert extract_with_llm("Other", "Berlin", client, _SOURCES) is None
    assert client.chat.completions.create.call_count == 1


def test_breaker_trips_on_permission_denied():
    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(PermissionDeniedError, 403, "error code: 1010")
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    assert breaker_open()


def test_breaker_tolerates_occasional_per_minute_limits():
    client = MagicMock()
    tpm = _status_error(RateLimitError, 429, "Rate limit reached on tokens per minute (TPM)")
    client.chat.completions.create.side_effect = [tpm, tpm]
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    assert not breaker_open()

    client.chat.completions.create.side_effect = None
    client.chat.completions.create.return_value.choices[0].message.content = "{}"
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    client.chat.completions.create.side_effect = [tpm, tpm]
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    extract_with_llm("Acme", "Berlin", client, _SOURCES)
    assert not breaker_open()  # success reset the consecutive counter


def test_breaker_trips_after_consecutive_per_minute_limits():
    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(RateLimitError, 429, "tokens per minute (TPM)")
    for _ in range(3):
        extract_with_llm("Acme", "Berlin", client, _SOURCES)
    assert breaker_open()
