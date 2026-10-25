import logging
import random
import re
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
import pandas as pd
from jobspy import scrape_jobs
from jobspy.indeed import Indeed
from jobspy.linkedin import LinkedIn
from jobspy.model import Country, ScraperInput, Site, DescriptionFormat
from ingestion.offer.scraping.filters import is_latin_script, _strip_html
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scopes import Scope
from ingestion.offer.scraping.stored import StoredJobs
from ingestion.offer.scraping.sources.base import Source

logger = logging.getLogger(__name__)

# LinkedIn jobs kept per run (skipped cards do not count). LinkedIn has hundreds of AI
# jobs in Europe every 4 hours; each card kept costs one more request (its description),
# so the cap is mostly how many requests a run sends: LinkedIn rate-limits bursts (429).
_LINKEDIN_CARDS = 80
# Seconds before each description request: jobspy fires them back to back.
_DETAIL_PAUSE = (1.0, 3.0)


class _Skipped(Exception):
    """A LinkedIn card left out. jobspy's search loop catches any exception from
    _process_job and moves to the next card, while a None would be appended and fail
    its JobResponse validation, losing the whole search."""


class _Blocked(Exception):
    """LinkedIn answered 429 earlier in this run: no further request is sent."""


# jobspy logs every caught exception as "skipping job: ..."; ours are expected
logging.getLogger("JobSpy:LinkedIn").addFilter(
    lambda record: not record.getMessage().startswith(f"skipping job: {_Skipped.__name__}"))
_INDEED_COUNTRIES = ["UK", "Germany", "France", "Netherlands", "Spain", "Italy", "Belgium", "Austria"]
# Glassdoor needs a concrete place ("Europe" resolves to France only), looked up by name
# on the country's own site: glassdoor.co.uk knows "United Kingdom", not "UK".
# country (jobspy's name) → location
_GLASSDOOR_LOCATIONS = {
    "UK": "United Kingdom",
    "Germany": "Germany",
    "France": "France",
    "Netherlands": "Netherlands",
}


# jobspy keeps only the day a job was posted. The boards say more: Indeed sends a
# timestamp, LinkedIn cards read "3 hours ago" next to the date. Freshness matters
# (applying early), so both are kept; a time that cannot be read leaves the day.
_AGO = re.compile(r"(\d+)\s+(minute|hour)s?\s+ago")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _card_posted_at(job_card) -> datetime | None:
    """'3 hours ago' on a LinkedIn card as a UTC time; None for days or an unreadable card."""
    try:
        m = _AGO.search(job_card.find("time").get_text(" ", strip=True).lower())
    except AttributeError:
        return None
    if not m:
        return None
    amount, unit = int(m.group(1)), m.group(2)
    return _utc_now() - (timedelta(minutes=amount) if unit == "minute" else timedelta(hours=amount))


class _SkippingLinkedIn(LinkedIn):
    """jobspy aborts the whole search when one card has a country outside its enum
    (e.g. 'north macedonia'); drop that card instead. Also keeps the card's hour, and
    skips cards whose title is off-scope or whose job is already stored (by URL, or by
    title + company) before their description is fetched (one request each), pausing before each fetch.

    The first 429 stops LinkedIn for the rest of the run: jobspy would carry on into the
    block, keeping jobs without a description (which the pipeline would then request
    again). The job that got the 429 and every card after it are dropped, so their URLs
    stay unstored and the next run picks them up. The other sites are separate scrapes."""

    def __init__(self, scope: Scope, stored: StoredJobs, **kwargs):
        super().__init__(**kwargs)
        self.job_scope = scope
        self.stored = stored
        self.blocked = False
        self._session_get = self.session.get
        self.session.get = self._get

    def _get(self, *args, **kwargs):
        if self.blocked:
            raise _Blocked("429 earlier in this run")
        response = self._session_get(*args, **kwargs)
        if response.status_code == 429:
            self.blocked = True
        return response

    def _process_job(self, job_card, job_id, *args):
        if self.blocked:
            raise _Skipped(f"{_Skipped.__name__}: blocked")
        title_tag = job_card.find("h3", class_="base-search-card__title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        company_tag = job_card.find("h4", class_="base-search-card__subtitle")
        company = company_tag.get_text(strip=True) if company_tag else ""
        if (not self.job_scope.matches(title) or self.stored.same_url(f"{self.base_url}/jobs/view/{job_id}")
                or self.stored.same_job(title, company)):
            raise _Skipped(f"{_Skipped.__name__}: off-scope or stored")
        time.sleep(random.uniform(*_DETAIL_PAUSE))  # super() fetches the description
        try:
            post = super()._process_job(job_card, job_id, *args)
        except ValueError as e:
            logger.info("LinkedIn job %s skipped: %s", job_id, str(e).split(". Valid")[0])
            raise _Skipped(f"{_Skipped.__name__}: {job_id}") from None
        if post is None or self.blocked:
            raise _Skipped(f"{_Skipped.__name__}: {job_id} gave no job")
        if posted := _card_posted_at(job_card):
            post.date_posted = posted
        return post


class _TimedIndeed(Indeed):
    """Indeed with the posting timestamp it sends (dateOnIndeed, ms) instead of the day."""

    def _process_job(self, job: dict):
        post = super()._process_job(job)
        try:
            post.date_posted = datetime.fromtimestamp(job["dateOnIndeed"] / 1000, timezone.utc).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError, OSError):
            pass
        return post


def _posted_at(value) -> datetime | None:
    """A posting time; a bare day becomes its midnight."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, dt_time.min)
    return None


def _text(value) -> str:
    """A clean string from a jobspy value; NaN, None and "nan" become "" (pandas NaN is truthy)."""
    if not isinstance(value, (str, int, float)) or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "null") else text


def _company_info(site: str, values: dict) -> dict[str, str]:
    """Employer facts the board ships with the job. Indeed's head count and revenue are
    left out: they are often wrong (Wolt listed as "11 to 50" employees)."""
    info = {"industry": _text(values.get("company_industry"))}
    if site == "linkedin":
        # country subdomains (ee.linkedin.com/company/x) → one canonical form
        if m := re.search(r"linkedin\.com/company/([^/?#]+)", _text(values.get("company_url"))):
            info["linkedin_url"] = f"https://www.linkedin.com/company/{m.group(1)}"
    else:
        info["website"] = _text(values.get("company_url_direct"))
        info["description"] = _text(values.get("company_description"))
    return {k: v for k, v in info.items() if v}


def _df_to_offers(df: pd.DataFrame) -> list[JobOffer]:
    results = []
    for _, row in df.iterrows():
        title = row.get("title")
        if not title or not isinstance(title, str):
            continue
        if not is_latin_script(title):
            continue
        posted_at: datetime | None = None
        raw_date = row.get("date_posted")
        if pd.notna(raw_date) and raw_date is not None:
            try:
                posted_at = datetime.combine(pd.Timestamp(raw_date).date(), dt_time.min)
            except Exception:
                pass
        site = row.get("site", "unknown")
        raw_desc_val = row.get("description")
        raw_desc = "" if pd.isna(raw_desc_val) else str(raw_desc_val or "")
        results.append(JobOffer(
            title=title,
            company=_text(row.get("company")),
            location=_text(row.get("location")),
            url=_text(row.get("job_url")),
            source=f"jobspy:{site}",
            posted_at=posted_at,
            description=_strip_html(raw_desc) or None,
            company_info=_company_info(site, row),
        ))
    return results


def _post_to_offer(job, site: str) -> JobOffer:
    """A jobspy JobPost (from a scraper called directly) as an offer."""
    location = job.location.display_location() if job.location else ""
    return JobOffer(
        title=job.title,
        company=_text(job.company_name),
        location=location,
        url=job.job_url,
        source=f"jobspy:{site}",
        posted_at=_posted_at(job.date_posted),
        description=_strip_html(job.description) if job.description else None,
        company_info=_company_info(site, {
            "company_url": job.company_url, "company_industry": job.company_industry,
            "company_url_direct": getattr(job, "company_url_direct", None),
            "company_description": getattr(job, "company_description", None),
        }),
    )


def _scrape_linkedin(scope: Scope, hours: int, stored: StoredJobs) -> list[JobOffer]:
    try:
        scraper = _SkippingLinkedIn(scope, stored)
        response = scraper.scrape(ScraperInput(
            site_type=[Site.LINKEDIN],
            search_term=scope.or_query(),
            location="Europe",
            hours_old=hours,
            results_wanted=_LINKEDIN_CARDS,
            fetch_description=True,
            description_format=DescriptionFormat.HTML,
        ))
        offers = [
            _post_to_offer(job, "linkedin")
            for job in response.jobs
            if job.title and is_latin_script(job.title)
        ]
        if scraper.blocked:
            logger.warning("LinkedIn answered 429: stopped for this run with %d jobs", len(offers))
        return offers
    except Exception as e:
        logger.warning("jobspy LinkedIn fetch failed: %s", e)
        return []


def _scrape_glassdoor(scope: Scope, hours: int, country: str) -> list[JobOffer]:
    try:
        df = scrape_jobs(
            site_name=["glassdoor"],
            search_term=scope.or_query(),
            location=_GLASSDOOR_LOCATIONS[country],
            country_indeed=country,
            hours_old=hours,
            results_wanted=25,
            fetch_description=True,
            verbose=0,
        )
        return _df_to_offers(df)
    except Exception as e:
        logger.warning("jobspy Glassdoor/%s fetch failed: %s", country, e)
        return []


def _scrape_indeed(scope: Scope, hours: int, country: str) -> list[JobOffer]:
    try:
        response = _TimedIndeed().scrape(ScraperInput(
            site_type=[Site.INDEED],
            search_term=scope.or_query(),
            country=Country.from_string(country),
            hours_old=hours,
            results_wanted=20,
            description_format=DescriptionFormat.HTML,
        ))
        return [_post_to_offer(job, "indeed") for job in response.jobs if job.title and is_latin_script(job.title)]
    except Exception as e:
        logger.warning("jobspy Indeed/%s fetch failed: %s", country, e)
        return []


class JobspySource(Source):
    def fetch(self, scope: Scope, hours: int, stored: StoredJobs = StoredJobs()) -> list[JobOffer]:
        results: list[JobOffer] = []
        results.extend(_scrape_linkedin(scope, hours, stored))
        for country in _GLASSDOOR_LOCATIONS:
            results.extend(_scrape_glassdoor(scope, hours, country))
        for country in _INDEED_COUNTRIES:
            results.extend(_scrape_indeed(scope, hours, country))
        return results
