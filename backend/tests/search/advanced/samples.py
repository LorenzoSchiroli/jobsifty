from backend.search.advanced.brief import BriefItem
from backend.search.advanced.observation import Candidate

REMOTE = BriefItem(text="remote only", kind="require", source="stated", field="location_type", value="remote")
NO_CONSULTING = BriefItem(text="no consulting", kind="avoid", source="stated", field="is_consulting", value=True)
STARTUP = BriefItem(text="startup", kind="prefer", source="inferred", field="is_startup", value=True)
IN_MILAN = BriefItem(text="in Milan", kind="require", source="stated")  # no field: the model judges it
MILAN = BriefItem(text="in Milan", kind="require", source="stated", field="place", value=["Milan"])


def candidate(job_id: str, score: float = 0.9, **kw) -> Candidate:
    """A Candidate with plausible defaults; keyword arguments override any field."""
    values = dict(
        job_id=job_id, score=score, title="ML Engineer", company="Acme", location="Berlin, Germany",
        job_cities=["berlin"], job_countries=["Germany"], company_country="DE", location_type="remote", seniority="senior",
        employment_type="full-time", languages=["english"], salary=None, is_startup=True, is_consulting=False,
        posted_at="2026-11-02T10:00:00+00:00", text="Build ranking models in PyTorch.", search_index=1,
    ) | kw
    return Candidate(**values)
