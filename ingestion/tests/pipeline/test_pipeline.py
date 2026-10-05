from ingestion.pipeline.__main__ import is_enrichment_needed


def test_all_none_needs_enrichment():
    company = {
        "id": "1", "name": "Acme", "website": None,
        "country": None, "founded_year": None, "employee_count": None,
    }
    assert is_enrichment_needed(company) is True


def test_exactly_half_none_needs_enrichment():
    # 3 None out of 6 = 50% — threshold is >=, so True
    company = {
        "id": "1", "name": "Acme", "website": "acme.com",
        "country": None, "founded_year": None, "employee_count": None,
    }
    assert is_enrichment_needed(company) is True


def test_majority_populated_no_enrichment():
    company = {
        "id": "1", "name": "Acme", "website": "acme.com",
        "country": "US", "founded_year": 2010, "employee_count": "100-500",
    }
    assert is_enrichment_needed(company) is False


def test_one_null_no_enrichment():
    # 1 out of 6 = 17% → False
    company = {
        "id": "1", "name": "Acme", "website": "acme.com",
        "country": "US", "founded_year": None, "employee_count": "100-500",
    }
    assert is_enrichment_needed(company) is False


def test_bool_false_is_not_null():
    company = {
        "id": "1", "name": "Acme", "is_consulting": False,
        "is_startup": False, "website": "acme.com", "country": "US",
    }
    assert is_enrichment_needed(company) is False


def test_identity_fields_not_counted():
    # id and name are always populated — should not dilute the null count
    company = {
        "id": "abc-123",
        "name": "Acme",
        "created_at": "2024-01-01",
        "updated_at": "2024-01-01",
        "website": None,
        "country": None,
        "founded_year": None,
        "employee_count": None,
    }
    # 4 None enrichable fields out of 4 enrichable = 100% → True
    assert is_enrichment_needed(company) is True


# --- run() ---

from unittest.mock import MagicMock, patch

from ingestion.offer.models import EnrichedOffer
from ingestion.pipeline.__main__ import has_enriched_fields, run


def _offer(i: int) -> EnrichedOffer:
    return EnrichedOffer(title=f"Job {i}", company="Acme", location="Berlin", url=f"https://x/{i}", source="t", posted_at=None)


def _profile(**fields):
    profile = MagicMock()
    profile.model_dump.return_value = {"name": "Acme", **fields}
    return profile


def test_has_enriched_fields():
    assert has_enriched_fields({"name": "Acme", "id": "1", "country": "de"})
    assert not has_enriched_fields({"name": "Acme", "id": "1", "country": None})


def test_run_without_groq_key_passes_no_client(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("ingestion.pipeline.__main__.enrich_all", return_value=[_offer(1)]) as mock_enrich, \
         patch("ingestion.pipeline.__main__.post_job", return_value=(201, {})), \
         patch("ingestion.pipeline.__main__.post_company", return_value=(201, {"id": "1", "name": "Acme", "country": None})), \
         patch("ingestion.pipeline.__main__.enrich_company", return_value=(_profile(country="de"), [])) as mock_company:
        assert run("q", 2) == 0
    mock_enrich.assert_called_once_with("q", 2)
    assert mock_company.call_args.args[2] is None


def test_run_does_not_post_empty_company_profile(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("ingestion.pipeline.__main__.enrich_all", return_value=[_offer(1)]), \
         patch("ingestion.pipeline.__main__.post_job", return_value=(201, {})), \
         patch("ingestion.pipeline.__main__.post_company", return_value=(201, {"id": "1", "name": "Acme", "country": None})) as mock_post_company, \
         patch("ingestion.pipeline.__main__.enrich_company", return_value=(_profile(country=None), [])):
        run("q", 2)
    assert mock_post_company.call_count == 1  # only the initial upsert


def test_run_exit_code_on_post_errors(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    offers = [_offer(i) for i in range(5)]
    with patch("ingestion.pipeline.__main__.enrich_all", return_value=offers), \
         patch("ingestion.pipeline.__main__.post_job", side_effect=[(201, {}), (201, {}), (201, {}), RuntimeError("x"), RuntimeError("y")]), \
         patch("ingestion.pipeline.__main__.post_company", return_value=(200, {"id": "1", "name": "Acme", "country": "de"})):
        assert run("q", 2) == 1


def test_run_exit_zero_under_error_threshold(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    offers = [_offer(i) for i in range(5)]
    with patch("ingestion.pipeline.__main__.enrich_all", return_value=offers), \
         patch("ingestion.pipeline.__main__.post_job", side_effect=[(201, {})] * 4 + [RuntimeError("x")]), \
         patch("ingestion.pipeline.__main__.post_company", return_value=(200, {"id": "1", "name": "Acme", "country": "de"})):
        assert run("q", 2) == 0
