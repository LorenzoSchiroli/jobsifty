from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scraping import scrape


class _Source:
    def __init__(self, offers):
        self.offers = offers

    def fetch(self, query, hours):
        return self.offers


def _offer(company: str, i: int) -> JobOffer:
    return JobOffer(title=f"Job {i}", company=company, location="", url=f"https://x/{i}", source="t", posted_at=None)


def test_scrape_drops_offers_without_a_company_name():
    offers = scrape("q", 2, sources=[_Source([_offer("Acme", 1), _offer("", 2), _offer("  ", 3)])])
    assert [o.company for o in offers] == ["Acme"]
