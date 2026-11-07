from dataclasses import dataclass, field
from datetime import datetime


def clean_location(value: str | None) -> str:
    """The location without its empty parts: some boards send ", Lombardia" or a bare ","
    when the city is missing (freehire's jobleads listings)."""
    return ", ".join(part.strip() for part in (value or "").split(",") if part.strip())


@dataclass
class JobOffer:
    title: str
    company: str
    location: str
    url: str
    source: str
    posted_at: datetime | None
    description: str | None = None
    # What the job board says about the employer: website, linkedin_url, industry,
    # description. Free with the scrape, so it is stored before any search runs.
    company_info: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.location = clean_location(self.location)
