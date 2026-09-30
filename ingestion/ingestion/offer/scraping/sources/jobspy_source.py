import logging
from datetime import datetime, time as dt_time
import pandas as pd
from jobspy import scrape_jobs
from jobspy.linkedin import LinkedIn
from jobspy.model import ScraperInput, Site, DescriptionFormat
from ingestion.offer.scraping.filters import is_english, _strip_html
from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.sources.base import Source

logger = logging.getLogger(__name__)

_INDEED_COUNTRIES = ["UK", "Germany", "France", "Netherlands", "Spain", "Italy", "Belgium", "Austria"]


class _SkippingLinkedIn(LinkedIn):
    """jobspy aborts the whole search when one card has a country outside its enum
    (e.g. 'north macedonia'); drop that card instead."""

    def _process_job(self, job_card, job_id, full_descr):
        try:
            return super()._process_job(job_card, job_id, full_descr)
        except ValueError as e:
            logger.info("LinkedIn job %s skipped: %s", job_id, str(e).split(". Valid")[0])
            return None


def _df_to_offers(df: pd.DataFrame) -> list[JobOffer]:
    results = []
    for _, row in df.iterrows():
        title = row.get("title")
        if not title or not isinstance(title, str):
            continue
        if not is_english(title):
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
            company=str(row.get("company") or ""),
            location=str(row.get("location") or ""),
            url=str(row.get("job_url") or ""),
            source=f"jobspy:{site}",
            posted_at=posted_at,
            description=_strip_html(raw_desc) or None,
        ))
    return results


def _linkedin_job_to_offer(job) -> JobOffer:
    location = job.location.display_location() if job.location else ""
    return JobOffer(
        title=job.title,
        company=job.company_name or "",
        location=location,
        url=job.job_url,
        source="jobspy:linkedin",
        posted_at=datetime.combine(job.date_posted, dt_time.min) if job.date_posted else None,
        description=_strip_html(job.description) if job.description else None,
    )


def _scrape_linkedin(query: str, hours: int) -> list[JobOffer]:
    try:
        scraper = _SkippingLinkedIn()
        response = scraper.scrape(ScraperInput(
            site_type=[Site.LINKEDIN],
            search_term=query,
            location="Europe",
            hours_old=hours,
            results_wanted=50,
            linkedin_fetch_description=True,
            description_format=DescriptionFormat.HTML,
        ))
        return [
            _linkedin_job_to_offer(job)
            for job in response.jobs
            if job.title and is_english(job.title)
        ]
    except Exception as e:
        logger.warning("jobspy LinkedIn fetch failed: %s", e)
        return []


def _scrape_glassdoor(query: str, hours: int) -> list[JobOffer]:
    try:
        df = scrape_jobs(
            site_name=["glassdoor"],
            search_term=query,
            location="Europe",
            hours_old=hours,
            results_wanted=50,
        )
        return _df_to_offers(df)
    except Exception as e:
        logger.warning("jobspy Glassdoor fetch failed: %s", e)
        return []


def _scrape_google(query: str, hours: int) -> list[JobOffer]:
    try:
        df = scrape_jobs(
            site_name=["google"],
            search_term=query,
            location="Europe",
            hours_old=hours,
            results_wanted=50,
        )
        return _df_to_offers(df)
    except Exception as e:
        logger.warning("jobspy Google Jobs fetch failed: %s", e)
        return []


def _scrape_indeed(query: str, hours: int, country: str) -> list[JobOffer]:
    try:
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=query,
            hours_old=hours,
            results_wanted=20,
            country_indeed=country,
        )
        return _df_to_offers(df)
    except Exception as e:
        logger.warning("jobspy Indeed/%s fetch failed: %s", country, e)
        return []


class JobspySource(Source):
    def fetch(self, query: str, hours: int) -> list[JobOffer]:
        results: list[JobOffer] = []
        results.extend(_scrape_linkedin(query, hours))
        results.extend(_scrape_glassdoor(query, hours))
        results.extend(_scrape_google(query, hours))
        for country in _INDEED_COUNTRIES:
            results.extend(_scrape_indeed(query, hours, country))
        return results
