from ingestion.offer.scraping.models import JobOffer, clean_location


def test_clean_location_drops_empty_parts():
    assert clean_location(", Lombardia") == "Lombardia"
    assert clean_location(",") == ""
    assert clean_location(" Milan ,, Italy ") == "Milan, Italy"
    assert clean_location(None) == ""


def test_clean_location_keeps_a_full_location():
    assert clean_location("Berlin, BE, DE") == "Berlin, BE, DE"


def test_job_offer_cleans_its_location():
    offer = JobOffer(title="t", company="c", location=", Piemonte", url="u", source="s", posted_at=None)
    assert offer.location == "Piemonte"
