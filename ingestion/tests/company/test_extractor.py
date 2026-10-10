import json

from ingestion.company.parsing.extractor import extract_jsonld, extract_with_llm, format_sources
from ingestion.llm import ChatProvider, LLMPool
from ingestion.pool import Unusable

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


_SOURCES = "Summary: Acme is a software company founded in 2010 in Berlin."


class _Stub(ChatProvider):
    """A provider answering from a script: strings are replies, exceptions are raised."""
    name = "stub"
    env_key = "STUB_KEY"
    default_model = "stub-model"

    def __init__(self, *replies):
        super().__init__("key")
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _pool(*providers) -> LLMPool:
    return LLMPool(list(providers))


def test_extract_with_llm_returns_parsed_fields():
    stub = _Stub(json.dumps({
        "country": "Germany",
        "founded_year": 2010,
        "employee_count": "51-200",
        "industry": "Software",
        "is_consulting": False,
        "is_startup": True,
        "financial_health_score": 4,
        "financial_health_rationale": "Revenue grew 12% YoY.",
    }))

    result = extract_with_llm("Acme", "Berlin", _pool(stub), _SOURCES)

    assert result.country == "Germany"
    assert result.is_consulting is False
    assert result.is_startup is True
    assert result.founded_year == 2010
    assert result.financial_health_score == 4


def test_an_unusable_answer_goes_to_the_next_provider():
    bad, good = _Stub("not json at all"), _Stub('{"country": "Germany"}')
    assert extract_with_llm("Acme", "Berlin", _pool(bad, good), _SOURCES).country == "Germany"
    assert bad.failed == 1 and not bad.disabled


def test_extract_with_llm_returns_none_when_every_provider_fails():
    first, second = _Stub(Unusable("HTTP 401")), _Stub("[1, 2]")
    assert extract_with_llm("Acme", "Berlin", _pool(first, second), _SOURCES) is None
    assert first.disabled


def test_extract_with_llm_returns_none_without_a_pool():
    assert extract_with_llm("Acme", "Berlin", None, _SOURCES) is None


def test_extract_with_llm_strips_markdown_code_block():
    stub = _Stub('```JSON\n{"country": "Germany"}\n```')
    assert extract_with_llm("Acme", "Berlin", _pool(stub), _SOURCES).country == "Germany"


def test_extract_with_llm_includes_sources_in_prompt():
    stub = _Stub("{}")
    extract_with_llm("Acme", "Berlin", _pool(stub), "- Revenue grew 12% YoY.")
    assert "Revenue grew 12% YoY." in stub.prompts[0]
    assert "Job location: Berlin" in stub.prompts[0]
    assert "never the parent group's total" in stub.prompts[0]


def test_extract_with_llm_skips_the_call_without_sources():
    stub = _Stub("{}")
    assert extract_with_llm("Acme", "Berlin", _pool(stub), "") is None
    assert stub.prompts == []


def test_extract_with_llm_drops_an_out_of_range_financial_score():
    stub = _Stub(json.dumps({"financial_health_score": 0, "financial_health_rationale": "No data."}))
    result = extract_with_llm("Acme", "Berlin", _pool(stub), _SOURCES)
    assert result.financial_health_score is None
    assert result.financial_health_rationale is None


def test_format_sources_keeps_the_input_small():
    text = format_sources("Acme makes anvils.", ["x" * 1000] * 8, "Board blurb", page_text="About us")
    assert text.startswith("Summary: Acme makes anvils.")
    assert "Job board description: Board blurb" in text
    assert text.count("\n- ") == 5
    assert "About us" not in text  # the page is only a fallback for a missing answer
    assert len(text) < 3000


def test_format_sources_uses_the_company_page_without_a_search_answer():
    assert format_sources(None, [], page_text="We build anvils.") == "Company website: We build anvils."
