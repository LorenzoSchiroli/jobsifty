from ingestion.offer.scraping.models import JobOffer
from ingestion.offer.scraping.scraping import scrape
from ingestion.offer.scraping.scopes import AI


class _Source:
    def __init__(self, offers):
        self.offers = offers
        self.calls = []

    def fetch(self, scope, hours, known_urls=frozenset()):
        self.calls.append((scope, hours, known_urls))
        return self.offers


def _offer(company: str, i: int, title: str = "AI Engineer") -> JobOffer:
    return JobOffer(title=title, company=company, location="", url=f"https://x/{i}", source="t", posted_at=None)


def test_scrape_drops_offers_without_a_company_name():
    offers = scrape(AI, 2, sources=[_Source([_offer("Acme", 1), _offer("", 2), _offer("  ", 3)])])
    assert [o.company for o in offers] == ["Acme"]


class _BrokenSource:
    def fetch(self, scope, hours, known_urls=frozenset()):
        raise KeyError("data")


def test_a_source_that_raises_does_not_stop_the_others():
    offers = scrape(AI, 2, sources=[_BrokenSource(), _Source([_offer("Acme", 1)])])
    assert [o.company for o in offers] == ["Acme"]


def test_scrape_keeps_only_titles_in_scope():
    source = _Source([_offer("A", 1, "Senior ML Engineer"), _offer("B", 2, "Junior Graphic Designer"),
                      _offer("C", 3, "Data Scientist")])
    offers = scrape(AI, 2, sources=[source])
    assert sorted(o.company for o in offers) == ["A", "C"]


def test_scrape_skips_jobs_already_stored_and_tells_the_source():
    source = _Source([_offer("A", 1), _offer("B", 2)])
    offers = scrape(AI, 2, sources=[source], known_urls=frozenset({"https://x/1"}))
    assert [o.company for o in offers] == ["B"]
    assert source.calls == [(AI, 2, frozenset({"https://x/1"}))]
