from dataclasses import dataclass, field
from datetime import datetime


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
