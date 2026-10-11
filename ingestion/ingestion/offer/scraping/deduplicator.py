import re
from ingestion.offer.scraping.models import JobOffer

_PRIORITY = {"jobspy": 0, "adzuna": 1, "arbeitnow": 2, "remotive": 3}


def _tc_key(title: str, company: str) -> tuple[str, str]:
    clean = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return clean(title), clean(company)


def deduplicate(offers: list[JobOffer]) -> list[JobOffer]:
    """One offer per title + company, the highest-priority source winning.

    Not by URL: each board has its own URL for a job, so a URL only repeats within
    one board, where title + company repeats too. Indeed and Glassdoor carry the job
    id in the query string (viewjob?jk=...), so a URL key without it merged them all.
    """
    sorted_offers = sorted(offers, key=lambda o: _PRIORITY.get(o.source.split(":")[0], 99))
    seen: set[tuple[str, str]] = set()
    result: list[JobOffer] = []

    for offer in sorted_offers:
        key = _tc_key(offer.title, offer.company)
        if key in seen:
            continue
        seen.add(key)
        result.append(offer)

    return result
