from datetime import date
from unittest.mock import MagicMock, patch
import pandas as pd
from ingestion.offer.scraping.sources.jobspy_source import JobspySource, _scrape_linkedin


def _make_job_post(title, job_id="123"):
    job = MagicMock()
    job.id = job_id
    job.title = title
    job.company_name = "SpyCorp"
    job.job_url = f"https://linkedin.com/jobs/view/{job_id}"
    job.location.display_location.return_value = "London, UK"
    job.date_posted = date(2026, 4, 19)
    job.description = None
    return job


def _mock_linkedin(jobs):
    mock_scraper = MagicMock()
    mock_scraper.scrape.return_value.jobs = jobs
    return patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper)


def _indeed_df():
    return pd.DataFrame([
        {
            "title": None,
            "company": "NullCo",
            "location": "Berlin",
            "job_url": "https://indeed.com/jobs/789",
            "date_posted": None,
            "site": "indeed",
            "description": None,
        },
    ])


# --- LinkedIn-specific tests ---

def test_linkedin_returns_english_offers_only():
    jobs = [_make_job_post("Python Engineer", "1"), _make_job_post("Ingénieur Python", "2")]

    with _mock_linkedin(jobs):
        results = _scrape_linkedin("python", hours=72)

    titles = [r.title for r in results]
    assert "Python Engineer" in titles
    assert "Ingénieur Python" not in titles
    assert all(r.source == "jobspy:linkedin" for r in results)


def test_linkedin_offers_have_no_description():
    """Scraping phase returns offers without descriptions — fetching is deferred to enrich_all."""
    jobs = [_make_job_post("Python Engineer", "1")]

    with _mock_linkedin(jobs):
        results = _scrape_linkedin("python", hours=72)

    assert results[0].description is None


def test_linkedin_failure_returns_empty_list():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        results = _scrape_linkedin("python", hours=72)

    assert results == []


# --- JobspySource integration tests ---

def test_returns_english_offers_from_all_calls():
    jobs = [_make_job_post("Python Engineer", "1"), _make_job_post("Ingénieur Python", "2")]

    with _mock_linkedin(jobs):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=_indeed_df()):
            results = JobspySource().fetch("python", hours=72)

    titles = [r.title for r in results]
    assert "Python Engineer" in titles
    assert "Ingénieur Python" not in titles
    assert all(r.source.startswith("jobspy:") for r in results)


def test_linkedin_failure_does_not_block_indeed():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=_indeed_df()):
            results = JobspySource().fetch("python", hours=72)

    assert results == []  # indeed df has no valid titles


def test_returns_empty_when_all_fail():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", side_effect=Exception("blocked")):
            assert JobspySource().fetch("python", hours=72) == []


def test_description_is_stripped_of_html():
    mock_df = pd.DataFrame([{
        "title": "Python Engineer",
        "company": "SpyCorp",
        "location": "London, UK",
        "job_url": "https://glassdoor.com/jobs/view/123",
        "date_posted": pd.Timestamp("2026-04-19"),
        "site": "glassdoor",
        "description": "<p>Build <b>microservices</b> in Python.</p>",
    }])

    with _mock_linkedin([]):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=mock_df):
            results = JobspySource().fetch("python", hours=72)

    matching = [r for r in results if r.title == "Python Engineer"]
    assert len(matching) >= 1
    assert "microservices" in matching[0].description
    assert "<" not in matching[0].description


def test_description_is_none_when_description_column_is_nan():
    import numpy as np

    mock_df = pd.DataFrame([{
        "title": "Python Engineer",
        "company": "SpyCorp",
        "location": "London, UK",
        "job_url": "https://glassdoor.com/jobs/view/999",
        "date_posted": pd.Timestamp("2026-04-19"),
        "site": "glassdoor",
        "description": np.nan,
    }])

    with _mock_linkedin([]):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=mock_df):
            results = JobspySource().fetch("python", hours=72)

    matching = [r for r in results if r.title == "Python Engineer"]
    assert len(matching) >= 1
    assert matching[0].description is None


def test_linkedin_unknown_country_skips_only_that_job():
    from jobspy.linkedin import LinkedIn
    from ingestion.offer.scraping.sources.jobspy_source import _SkippingLinkedIn

    def fake_process(self, job_card, job_id, full_descr):
        if job_id == "bad":
            raise ValueError("Invalid country string: 'north macedonia'. Valid countries are: ...")
        return job_id

    with patch.object(LinkedIn, "_process_job", fake_process):
        scraper = _SkippingLinkedIn()
        assert scraper._process_job(None, "ok", False) == "ok"
        assert scraper._process_job(None, "bad", False) is None
