from ingestion.offer.models import OfferExtraction
from ingestion.offer.parsing import rules
from ingestion.offer.parsing.sections import clean_markdown, split_sections
from ingestion.offer.scraping.models import JobOffer


def parse(offer: JobOffer) -> OfferExtraction:
    return parse_with_text(offer)[0]


def parse_with_text(offer: JobOffer) -> tuple[OfferExtraction, str]:
    """Deterministic extraction of the structured fields with rules.

    Also returns the boilerplate-stripped text the embedder should chunk.
    """
    description = clean_markdown(offer.description)
    sections = split_sections(description)
    body = sections.kept_text or description

    # Work arrangement and contract terms often sit in the benefits section, so use the full text.
    location_type = rules.location_type(offer.title, offer.location, description)
    text_language = rules.text_language(description)
    extraction = OfferExtraction(
        employment_type=rules.employment_type(offer.title, description),
        location_type=location_type,
        office=rules.office(offer.location, location_type),
        seniority=rules.seniority(offer.title, body),
        salary_range=rules.salary_range(f"{offer.title}\n{description}"),
        languages_required=rules.languages_required(body, text_language),
        text_language=text_language,
    )
    return extraction, body
