from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from bs4 import BeautifulSoup
from ingestion.offer.scraping.sources.jobspy_source import JobspySource, _scrape_linkedin


@pytest.fixture(autouse=True)
def _no_real_indeed():
    """Indeed is called as a scraper class; no test may reach the real site."""
    scraper = MagicMock()
    scraper.scrape.return_value.jobs = []
    with patch("ingestion.offer.scraping.sources.jobspy_source._TimedIndeed", return_value=scraper) as cls:
        yield cls


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

def test_linkedin_returns_latin_script_offers_only():
    jobs = [_make_job_post("Python Engineer", "1"), _make_job_post("Python инженер", "2")]

    with _mock_linkedin(jobs):
        results = _scrape_linkedin(AI, 72, frozenset())

    titles = [r.title for r in results]
    assert "Python Engineer" in titles
    assert "Python инженер" not in titles
    assert all(r.source == "jobspy:linkedin" for r in results)


def test_linkedin_offers_have_no_description():
    """Scraping phase returns offers without descriptions — fetching is deferred to enrich_all."""
    jobs = [_make_job_post("Python Engineer", "1")]

    with _mock_linkedin(jobs):
        results = _scrape_linkedin(AI, 72, frozenset())

    assert results[0].description is None


def test_linkedin_failure_returns_empty_list():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        results = _scrape_linkedin(AI, 72, frozenset())

    assert results == []


# --- JobspySource integration tests ---

def test_returns_latin_script_offers_from_all_calls():
    jobs = [_make_job_post("Python Engineer", "1"), _make_job_post("Python инженер", "2")]

    with _mock_linkedin(jobs):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=_indeed_df()):
            results = JobspySource().fetch(AI, hours=72)

    titles = [r.title for r in results]
    assert "Python Engineer" in titles
    assert "Python инженер" not in titles
    assert all(r.source.startswith("jobspy:") for r in results)


def test_linkedin_failure_does_not_block_indeed():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=_indeed_df()):
            results = JobspySource().fetch(AI, hours=72)

    assert results == []  # indeed df has no valid titles


def test_returns_empty_when_all_fail():
    mock_scraper = MagicMock()
    mock_scraper.scrape.side_effect = Exception("blocked")

    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper):
        with patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", side_effect=Exception("blocked")):
            assert JobspySource().fetch(AI, hours=72) == []


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
            results = JobspySource().fetch(AI, hours=72)

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
            results = JobspySource().fetch(AI, hours=72)

    matching = [r for r in results if r.title == "Python Engineer"]
    assert len(matching) >= 1
    assert matching[0].description is None


def test_linkedin_unknown_country_skips_only_that_job():
    from jobspy.linkedin import LinkedIn
    from ingestion.offer.scraping.sources.jobspy_source import _Skipped, _SkippingLinkedIn

    def fake_process(self, job_card, job_id):
        if job_id == "bad":
            raise ValueError("Invalid country string: 'north macedonia'. Valid countries are: ...")
        return job_id

    with patch.object(LinkedIn, "_process_job", fake_process):
        scraper = _SkippingLinkedIn(AI, frozenset())
        assert scraper._process_job(_card(""), "ok") == "ok"
        with pytest.raises(_Skipped):
            scraper._process_job(_card(""), "bad")


# --- company facts and missing names ---

from ingestion.offer.scraping.sources.jobspy_source import _df_to_offers, _post_to_offer


def test_linkedin_offer_carries_canonical_company_page_and_industry():
    job = _make_job_post("Data Engineer")
    job.company_url = "https://ee.linkedin.com/company/markit-eu?trk=public_jobs"
    job.company_industry = "IT Services and IT Consulting"
    offer = _post_to_offer(job, "linkedin")
    assert offer.company_info == {
        "linkedin_url": "https://www.linkedin.com/company/markit-eu",
        "industry": "IT Services and IT Consulting",
    }


def test_indeed_offer_carries_website_and_description_but_not_head_count():
    job = _make_job_post("Data Engineer")
    job.company_url = "https://de.indeed.com/cmp/Wolt"
    job.company_url_direct = "https://www.wolt.com"
    job.company_industry = "Transport And Freight"
    job.company_description = "Wolt delivers food."
    job.company_num_employees = "11 to 50"
    offer = _post_to_offer(job, "indeed")
    assert offer.source == "jobspy:indeed"
    assert offer.company_info == {
        "website": "https://www.wolt.com", "industry": "Transport And Freight", "description": "Wolt delivers food.",
    }


def test_missing_company_name_is_empty_not_nan():
    df = pd.DataFrame([{
        "title": "Data Engineer", "company": float("nan"), "location": float("nan"), "job_url": "https://indeed.com/2",
        "date_posted": None, "site": "indeed", "description": None,
    }])
    offer = _df_to_offers(df)[0]
    assert offer.company == ""
    assert offer.location == ""


def test_glassdoor_is_searched_per_country_not_europe():

    mock_scraper = MagicMock()
    mock_scraper.scrape.return_value = MagicMock(jobs=[])
    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper), \
         patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=pd.DataFrame()) as mock_scrape:
        JobspySource().fetch(AI, hours=72)

    glassdoor = [c.kwargs for c in mock_scrape.call_args_list if c.kwargs["site_name"] == ["glassdoor"]]
    assert [c["country_indeed"] for c in glassdoor] == ["UK", "Germany", "France", "Netherlands"]
    # Glassdoor's location lookup does not know "UK"
    assert [c["location"] for c in glassdoor] == ["United Kingdom", "Germany", "France", "Netherlands"]
    assert all(c["fetch_description"] for c in glassdoor)
    assert not any(c.kwargs["site_name"] == ["google"] for c in mock_scrape.call_args_list)


def test_skipping_linkedin_matches_the_installed_jobspy():
    """jobspy changed _process_job's arguments once (1.3.0), which silently emptied LinkedIn."""
    import inspect
    from jobspy.linkedin import LinkedIn

    params = list(inspect.signature(LinkedIn._process_job).parameters)
    assert params[:3] == ["self", "job_card", "job_id"]


# --- posting times ---

from ingestion.offer.scraping.sources.jobspy_source import _card_posted_at, _TimedIndeed
from ingestion.offer.scraping.scopes import AI


def _card(text: str, title: str = "AI Engineer"):
    return BeautifulSoup(f'<div><h3 class="base-search-card__title">{title}</h3>'
                         f'<time datetime="2026-10-16">{text}</time></div>', "html.parser")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.parametrize("text, ago", [("3 hours ago", timedelta(hours=3)), ("1 hour ago", timedelta(hours=1)),
                                       ("25 minutes ago", timedelta(minutes=25))])
def test_linkedin_card_hours_and_minutes_give_a_time(text, ago):
    assert abs(_card_posted_at(_card(text)) - (_utcnow() - ago)) < timedelta(seconds=5)


@pytest.mark.parametrize("text", ["2 days ago", "1 week ago", ""])
def test_linkedin_card_days_or_nothing_keep_the_day(text):
    assert _card_posted_at(_card(text)) is None
    assert _card_posted_at(BeautifulSoup("<div></div>", "html.parser")) is None


def test_linkedin_job_takes_the_card_time():
    from jobspy.linkedin import LinkedIn
    from ingestion.offer.scraping.sources.jobspy_source import _SkippingLinkedIn

    def fake_process(self, job_card, job_id):
        post = MagicMock()
        post.date_posted = date(2026, 10, 16)
        return post

    with patch.object(LinkedIn, "_process_job", fake_process):
        post = _SkippingLinkedIn(AI, frozenset())._process_job(_card("2 hours ago"), "1")
    assert abs(post.date_posted - (_utcnow() - timedelta(hours=2))) < timedelta(seconds=5)


def test_indeed_job_keeps_the_timestamp():
    from jobspy.indeed import Indeed

    def fake_process(self, job):
        post = MagicMock()
        post.date_posted = date(2026, 10, 16)
        return post

    stamp = datetime(2026, 10, 16, 13, 5, tzinfo=timezone.utc)
    with patch.object(Indeed, "_process_job", fake_process):
        post = _TimedIndeed._process_job(object.__new__(_TimedIndeed), {"dateOnIndeed": stamp.timestamp() * 1000})
    assert post.date_posted == datetime(2026, 10, 16, 13, 5)


def test_offer_posted_at_is_the_time_or_the_day_at_midnight():
    job = _make_job_post("Data Engineer")
    job.date_posted = datetime(2026, 10, 16, 13, 5)
    assert _post_to_offer(job, "indeed").posted_at == datetime(2026, 10, 16, 13, 5)
    job.date_posted = date(2026, 10, 16)
    assert _post_to_offer(job, "glassdoor").posted_at == datetime(2026, 10, 16)


def test_indeed_is_searched_per_country_with_html_descriptions(_no_real_indeed):
    with _mock_linkedin([]), patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=pd.DataFrame()):
        JobspySource().fetch(AI, hours=72)
    inputs = [c.args[0] for c in _no_real_indeed.return_value.scrape.call_args_list]
    assert [i.country.name for i in inputs] == ["UK", "GERMANY", "FRANCE", "NETHERLANDS", "SPAIN", "ITALY", "BELGIUM", "AUSTRIA"]
    assert all(i.description_format.name == "HTML" and i.hours_old == 72 for i in inputs)


def test_timed_indeed_matches_the_installed_jobspy():
    import inspect
    from jobspy.indeed import Indeed

    assert list(inspect.signature(Indeed._process_job).parameters)[:2] == ["self", "job"]


def test_linkedin_skips_off_scope_and_stored_cards_before_fetching_details():
    from jobspy.linkedin import LinkedIn
    from ingestion.offer.scraping.sources.jobspy_source import _Skipped, _SkippingLinkedIn

    processed = []

    def fake_process(self, job_card, job_id):
        processed.append(job_id)  # the parent fetches the description here
        return MagicMock(date_posted=None)

    scraper = _SkippingLinkedIn(AI, frozenset({f"{LinkedIn.base_url}/jobs/view/stored"}))
    with patch.object(LinkedIn, "_process_job", fake_process):
        with pytest.raises(_Skipped):
            scraper._process_job(_card("", "Junior Graphic Designer"), "off")
        with pytest.raises(_Skipped):
            scraper._process_job(_card("", "ML Engineer"), "stored")
        assert scraper._process_job(_card("", "ML Engineer"), "new") is not None
    assert processed == ["new"]


def test_linkedin_search_keeps_the_jobs_around_skipped_cards(caplog):
    """Through jobspy's own search loop: a skipped card once failed the whole search."""
    from jobspy.linkedin import LinkedIn
    from jobspy.model import JobPost, Location, ScraperInput, Site
    from ingestion.offer.scraping.sources.jobspy_source import _SkippingLinkedIn

    cards = "".join(
        f'<div class="base-search-card" data-entity-urn="urn:li:jobPosting:{i}">'
        f'<h3 class="base-search-card__title">{title}</h3><time datetime="2026-10-16"></time></div>'
        for i, title in [("1", "ML Engineer"), ("2", "Graphic Designer"), ("3", "ML Engineer"),
                         ("4", "AI Researcher")]
    )
    page = MagicMock(status_code=200, text=cards)

    def fake_process(self, job_card, job_id):
        return JobPost(id=job_id, title=job_card.find("h3").get_text(), company_name="Co",
                       job_url=f"{LinkedIn.base_url}/jobs/view/{job_id}", location=Location())

    scraper = _SkippingLinkedIn(AI, frozenset({f"{LinkedIn.base_url}/jobs/view/3"}))
    with patch.object(LinkedIn, "_process_job", fake_process), \
         patch.object(scraper.session, "get", return_value=page):
        response = scraper.scrape(ScraperInput(site_type=[Site.LINKEDIN], search_term="x", results_wanted=2))
    assert [job.id for job in response.jobs] == ["1", "4"]  # 2 off-scope, 3 stored: not counted
    assert "skipping job" not in caplog.text


def test_jobspy_sites_search_the_scope_or_query():
    mock_scraper = MagicMock()
    mock_scraper.scrape.return_value = MagicMock(jobs=[])
    with patch("ingestion.offer.scraping.sources.jobspy_source._SkippingLinkedIn", return_value=mock_scraper) as li, \
         patch("ingestion.offer.scraping.sources.jobspy_source.scrape_jobs", return_value=pd.DataFrame()) as gd:
        JobspySource().fetch(AI, 4, frozenset({"u"}))
    assert li.call_args.args == (AI, frozenset({"u"}))
    assert mock_scraper.scrape.call_args.args[0].search_term == AI.or_query()
    assert mock_scraper.scrape.call_args.args[0].results_wanted == 150
    assert gd.call_args.kwargs["search_term"] == AI.or_query()
