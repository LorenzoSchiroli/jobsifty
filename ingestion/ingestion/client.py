import os
import requests
from ingestion.offer.models import EnrichedOffer


def _base() -> str:
    url = os.environ.get("BACKEND_URL")
    if not url:
        raise RuntimeError("BACKEND_URL environment variable is not set")
    return url.rstrip("/")


def post_job(offer: EnrichedOffer) -> tuple[int, dict]:
    """Upsert a job; the payload carries `chunks`."""
    payload = offer.model_dump(mode="json", exclude={"company_info"})
    payload["company_name"] = payload.pop("company")
    resp = requests.post(f"{_base()}/jobs/", json=payload, timeout=30)
    if not resp.ok:
        raise requests.HTTPError(f"{resp.status_code} — {resp.text}", response=resp)
    return resp.status_code, resp.json()


def list_jobs(after: str | None = None, limit: int = 500) -> tuple[list[dict], str | None]:
    """One keyset page of stored jobs: (items, next_after)."""
    params: dict = {"limit": limit}
    if after:
        params["after"] = after
    resp = requests.get(f"{_base()}/jobs/", params=params, timeout=60)
    if not resp.ok:
        raise requests.HTTPError(f"{resp.status_code} — {resp.text}", response=resp)
    body = resp.json()
    return body["items"], body.get("next_after")


def post_company(data: dict) -> tuple[int, dict]:
    resp = requests.post(f"{_base()}/companies/", json=data, timeout=30)
    if not resp.ok:
        raise requests.HTTPError(f"{resp.status_code} — {resp.text}", response=resp)
    return resp.status_code, resp.json()


def get_pending_companies(limit: int) -> list[dict]:
    """Companies due for enrichment, newest job first (name, website, linkedin_url, industry, location)."""
    resp = requests.get(f"{_base()}/companies/pending", params={"limit": limit}, timeout=30)
    if not resp.ok:
        raise requests.HTTPError(f"{resp.status_code} — {resp.text}", response=resp)
    return resp.json()
