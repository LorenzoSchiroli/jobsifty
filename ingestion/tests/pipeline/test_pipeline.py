from unittest.mock import MagicMock, patch

import pytest

from ingestion.company.scraping import searcher
from ingestion.offer.models import EnrichedOffer
from ingestion.pipeline.__main__ import _collect_companies, new_fields, run

_P = "ingestion.pipeline.__main__"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    for key in ("TINYFISH_API_KEY", "EXA_API_KEY", "SEARCH_PROVIDERS", "COMPANY_ENRICH_PER_RUN",
                "NVIDIA_NIM_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)
    searcher.reset()
    yield
    searcher.reset()


def _offer(i: int, company: str = "Acme", location: str = "Berlin", **info) -> EnrichedOffer:
    return EnrichedOffer(title=f"Job {i}", company=company, location=location, url=f"https://x/{i}",
                         source="t", posted_at=None, company_info=info)


def _profile(**fields):
    profile = MagicMock()
    profile.model_dump.return_value = {"name": "acme", **fields}
    return profile


def test_collect_companies_merges_location_and_board_facts():
    offers = [
        _offer(1, location=""),
        _offer(2, location="Berlin", industry="Software"),
        _offer(3, location="Munich", website="https://acme.com", industry="Other"),
        _offer(4, company="Beta", linkedin_url="https://www.linkedin.com/company/beta"),
    ]
    assert _collect_companies(offers) == {
        "Acme": {"location": "Berlin", "industry": "Software", "website": "https://acme.com"},
        "Beta": {"location": "Berlin", "linkedin_url": "https://www.linkedin.com/company/beta"},
    }


def test_board_websites_are_checked_like_searched_ones():
    offers = [
        _offer(1, company="Zendesk", website="https://zendesk.wd1.myworkdayjobs.com/zendesk"),
        _offer(2, company="Criteo", website="https://careers.criteo.com/en"),
    ]
    companies = _collect_companies(offers)
    assert "website" not in companies["Zendesk"]
    assert companies["Criteo"]["website"] == "https://www.criteo.com"


def test_new_fields_ignores_what_was_known():
    known = {"website": "https://acme.com", "industry": "Software"}
    profile = {"name": "acme", "website": "https://acme.com", "industry": "Software", "founded_year": 2010, "topic": None}
    assert new_fields(profile, known) == ["founded_year"]


def test_run_upserts_companies_with_board_facts_then_enriches_pending():
    pending = [{"name": "acme", "website": None, "linkedin_url": None, "industry": None,
                "location": "Berlin", "live_jobs": 2}]
    with patch(f"{_P}.enrich_all", return_value=[_offer(1, website="https://acme.com")]), \
         patch(f"{_P}.post_job", return_value=(201, {})), \
         patch(f"{_P}.post_company", return_value=(201, {})) as mock_post_company, \
         patch(f"{_P}.get_pending_companies", return_value=pending) as mock_pending, \
         patch(f"{_P}.enrich_company", return_value=(_profile(founded_year=2010), [])) as mock_enrich:
        assert run("q", 2) == 0

    assert mock_post_company.call_args_list[0].args[0] == {"name": "Acme", "website": "https://acme.com"}
    mock_pending.assert_called_once_with(15)
    name, location, client, known = mock_enrich.call_args.args
    assert (name, location, client) == ("acme", "Berlin", None)
    assert known["website"] == "https://acme.com"  # this run's board facts reach the enrichment
    assert mock_post_company.call_args_list[1].args[0] == {"name": "acme", "founded_year": 2010, "enrichment_attempted": True}


def test_attempt_is_recorded_even_when_nothing_is_found():
    pending = [{"name": "acme", "location": "", "live_jobs": 1}]
    with patch(f"{_P}.enrich_all", return_value=[_offer(1)]), \
         patch(f"{_P}.post_job", return_value=(201, {})), \
         patch(f"{_P}.post_company", return_value=(201, {})) as mock_post_company, \
         patch(f"{_P}.get_pending_companies", return_value=pending), \
         patch(f"{_P}.enrich_company", return_value=(_profile(), [])):
        run("q", 2)
    assert mock_post_company.call_args_list[-1].args[0] == {"name": "acme", "enrichment_attempted": True}


def test_budget_comes_from_the_environment_and_zero_disables(monkeypatch):
    monkeypatch.setenv("COMPANY_ENRICH_PER_RUN", "0")
    with patch(f"{_P}.enrich_all", return_value=[_offer(1)]), \
         patch(f"{_P}.post_job", return_value=(201, {})), \
         patch(f"{_P}.post_company", return_value=(201, {})), \
         patch(f"{_P}.get_pending_companies") as mock_pending:
        assert run("q", 2) == 0
    mock_pending.assert_not_called()


def test_company_failures_never_fail_the_run():
    with patch(f"{_P}.enrich_all", return_value=[_offer(1)]), \
         patch(f"{_P}.post_job", return_value=(201, {})), \
         patch(f"{_P}.post_company", side_effect=RuntimeError("backend down")), \
         patch(f"{_P}.get_pending_companies", side_effect=RuntimeError("backend down")):
        assert run("q", 2) == 0


def test_enrichment_error_for_one_company_does_not_stop_the_next():
    pending = [{"name": "a", "location": "", "live_jobs": 2}, {"name": "b", "location": "", "live_jobs": 1}]
    with patch(f"{_P}.enrich_all", return_value=[]), \
         patch(f"{_P}.post_company", return_value=(200, {})) as mock_post_company, \
         patch(f"{_P}.get_pending_companies", return_value=pending), \
         patch(f"{_P}.enrich_company", side_effect=[RuntimeError("boom"), (_profile(country="DE"), [])]):
        assert run("q", 2) == 0
    assert mock_post_company.call_count == 1


def test_run_exit_code_on_post_errors():
    offers = [_offer(i) for i in range(5)]
    with patch(f"{_P}.enrich_all", return_value=offers), \
         patch(f"{_P}.post_job", side_effect=[(201, {}), (201, {}), (201, {}), RuntimeError("x"), RuntimeError("y")]), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=[]):
        assert run("q", 2) == 1


def test_run_exit_zero_under_error_threshold():
    offers = [_offer(i) for i in range(5)]
    with patch(f"{_P}.enrich_all", return_value=offers), \
         patch(f"{_P}.post_job", side_effect=[(201, {})] * 4 + [RuntimeError("x")]), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=[]):
        assert run("q", 2) == 0


def test_a_stored_website_that_is_not_the_company_is_not_used():
    pending = [
        {"name": "dex", "website": "https://find-and-update.company-information.service.gov.uk/company/OC1",
         "location": "", "live_jobs": 2},
        {"name": "cgi", "website": "https://www.cgi.com/uk/en-gb", "location": "", "live_jobs": 1},
    ]
    with patch(f"{_P}.enrich_all", return_value=[]), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=pending), \
         patch(f"{_P}.enrich_company", return_value=(_profile(), [])) as mock_enrich:
        run("q", 2)
    assert "website" not in mock_enrich.call_args_list[0].args[3]
    assert mock_enrich.call_args_list[1].args[3]["website"] == "https://www.cgi.com/uk/en-gb"


def _pending(n: int) -> list[dict]:
    return [{"name": f"c{i}", "location": "", "live_jobs": 1} for i in range(n)]


def test_companies_wait_for_the_next_run_once_search_is_down():
    def enrich(*args):
        for provider in searcher.pool().providers:  # e.g. Tavily, the only key, said 432
            provider.disabled = True
        return _profile(), []

    with patch(f"{_P}.enrich_all", return_value=[]), \
         patch(f"{_P}.post_company", return_value=(200, {})) as mock_post_company, \
         patch(f"{_P}.get_pending_companies", return_value=_pending(3)), \
         patch(f"{_P}.enrich_company", side_effect=enrich) as mock_enrich:
        assert run("q", 2) == 0
    assert mock_enrich.call_count == 1 and mock_post_company.call_count == 1


def test_companies_wait_for_the_next_run_once_the_llm_is_down():
    llm = MagicMock()
    llm.configured.return_value = ["nvidia"]
    llm.down.side_effect = [False, True]  # e.g. every provider's daily quota gone during the first company
    with patch(f"{_P}.LLMPool.from_env", return_value=llm), \
         patch(f"{_P}.enrich_all", return_value=[]), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=_pending(3)), \
         patch(f"{_P}.enrich_company", return_value=(_profile(), [])) as mock_enrich:
        assert run("q", 2) == 0
    assert mock_enrich.call_count == 1


def test_no_search_key_still_enriches(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    with patch(f"{_P}.enrich_all", return_value=[]), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=_pending(2)), \
         patch(f"{_P}.enrich_company", return_value=(_profile(), [])) as mock_enrich:
        assert run("q", 2) == 0
    assert mock_enrich.call_count == 2


def test_a_refresh_ignores_the_stored_values():
    pending = [{"name": "synthesia", "website": "http://www.synthesia.eu", "industry": "chemical industry",
                "location": "London", "live_jobs": 1, "refresh": True}]
    with patch(f"{_P}.enrich_all", return_value=[_offer(1, company="synthesia", industry="Software")]), \
         patch(f"{_P}.post_job", return_value=(201, {})), \
         patch(f"{_P}.post_company", return_value=(200, {})), \
         patch(f"{_P}.get_pending_companies", return_value=pending), \
         patch(f"{_P}.enrich_company", return_value=(_profile(), [])) as mock_enrich:
        run("q", 2)
    assert mock_enrich.call_args.args[3] == {"industry": "Software"}  # this run's board facts only
