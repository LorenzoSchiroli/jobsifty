from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.deduplicator import deduplicate


def _offer(title="Engineer", company="Acme", url="https://example.com/1", source="adzuna"):
    return JobOffer(title=title, company=company, location="London", url=url, source=source, posted_at=None)


def test_no_duplicates_returns_all():
    offers = [_offer(url="https://example.com/1"), _offer(url="https://example.com/2", title="Designer")]
    assert len(deduplicate(offers)) == 2


def test_same_url_deduped():
    offers = [_offer(url="https://example.com/1", source="adzuna"), _offer(url="https://example.com/1", source="remotive")]
    assert len(deduplicate(offers)) == 1


def test_url_trailing_slash_deduped():
    offers = [_offer(url="https://example.com/1/", source="adzuna"), _offer(url="https://example.com/1", source="remotive")]
    assert len(deduplicate(offers)) == 1


def test_same_title_company_deduped():
    offers = [
        _offer(url="https://site1.com/1", title="ML Engineer", company="Acme", source="jobspy:linkedin"),
        _offer(url="https://site2.com/9", title="ML Engineer", company="Acme", source="remotive"),
    ]
    assert len(deduplicate(offers)) == 1


def test_jobspy_preferred_over_remotive():
    offers = [
        _offer(url="https://example.com/1", source="remotive"),
        _offer(url="https://example.com/1", source="jobspy:linkedin"),
    ]
    result = deduplicate(offers)
    assert result[0].source == "jobspy:linkedin"


def test_adzuna_preferred_over_arbeitnow():
    offers = [
        _offer(url="https://example.com/1", source="arbeitnow"),
        _offer(url="https://example.com/1", source="adzuna"),
    ]
    result = deduplicate(offers)
    assert result[0].source == "adzuna"


def test_indeed_jobs_differing_only_in_query_string_are_kept():
    offers = [
        _offer(url="https://de.indeed.com/viewjob?jk=a1b2c3", title="ML Engineer", company="Zalando", source="jobspy:indeed"),
        _offer(url="https://de.indeed.com/viewjob?jk=d4e5f6", title="Data Scientist", company="N26", source="jobspy:indeed"),
    ]
    assert len(deduplicate(offers)) == 2
