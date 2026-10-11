import pytest


async def test_create_company_returns_201(client):
    resp = await client.post("/companies/", json={"name": "Stripe", "industry": "fintech"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "stripe"
    assert data["industry"] == "fintech"
    assert "id" in data


async def test_create_company_normalizes_name(client):
    resp = await client.post("/companies/", json={"name": "  Stripe  "})
    assert resp.status_code == 201
    assert resp.json()["name"] == "stripe"


async def test_upsert_company_fills_null_fields(client):
    await client.post("/companies/", json={"name": "Stripe"})
    resp = await client.post("/companies/", json={"name": "Stripe", "industry": "fintech", "country": "US"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["industry"] == "fintech"
    assert data["country"] == "US"


async def test_upsert_company_does_not_overwrite_existing_fields(client):
    await client.post("/companies/", json={"name": "Stripe", "industry": "fintech"})
    resp = await client.post("/companies/", json={"name": "Stripe", "industry": "payments"})
    assert resp.status_code == 200
    assert resp.json()["industry"] == "fintech"


async def test_get_company_returns_200(client):
    create_resp = await client.post("/companies/", json={"name": "Stripe"})
    company_id = create_resp.json()["id"]
    resp = await client.get(f"/companies/{company_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == company_id


async def test_get_company_returns_404_when_not_found(client):
    resp = await client.get("/companies/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


from sqlalchemy import select
from backend.models import Outbox


async def test_company_upsert_creates_outbox_event(client, db_session):
    await client.post("/companies/", json={"name": "TestCo", "industry": "tech"})
    result = await db_session.execute(
        select(Outbox).where(Outbox.event_type == "company_upserted")
    )
    events = result.scalars().all()
    assert len(events) == 1


async def test_upsert_company_stores_linkedin_url_and_topic(client):
    resp = await client.post("/companies/", json={
        "name": "Stripe", "linkedin_url": "https://www.linkedin.com/company/stripe", "topic": "Payments",
    })
    assert resp.json()["linkedin_url"] == "https://www.linkedin.com/company/stripe"
    assert resp.json()["topic"] == "Payments"


async def test_enrichment_attempt_is_counted_even_without_data(client):
    first = (await client.post("/companies/", json={"name": "Stripe"})).json()
    assert first["enrich_attempts"] == 0 and first["enriched_at"] is None
    resp = await client.post("/companies/", json={"name": "Stripe", "enrichment_attempted": True})
    assert resp.json()["enrich_attempts"] == 1
    assert resp.json()["enriched_at"] is not None


async def _post_job(client, company: str, i: int, location: str = "Berlin, Germany"):
    resp = await client.post("/jobs/", json={
        "url": f"https://jobs.example/{company}/{i}", "title": "Engineer", "company_name": company, "location": location,
    })
    assert resp.status_code == 201


async def test_pending_orders_by_newest_job_and_returns_location(client):
    for i in range(3):
        await _post_job(client, "Big", i, location="Paris, France")
    await _post_job(client, "Small", 1)
    resp = await client.get("/companies/pending", params={"limit": 10})
    assert resp.status_code == 200
    assert [(c["name"], c["live_jobs"], c["location"]) for c in resp.json()] == [
        ("small", 1, "Berlin, Germany"), ("big", 3, "Paris, France"),
    ]


async def test_pending_skips_recently_attempted_and_companies_without_live_jobs(client):
    await _post_job(client, "Tried", 1)
    await client.post("/companies/", json={"name": "Tried", "enrichment_attempted": True})
    await client.post("/companies/", json={"name": "NoJobs"})
    assert (await client.get("/companies/pending")).json() == []


async def test_pending_retries_an_empty_company_after_the_pause(client, db_session):
    from datetime import datetime, timedelta, timezone
    from backend.models import Company
    await _post_job(client, "Empty", 1)
    await _post_job(client, "Filled", 1)
    await client.post("/companies/", json={"name": "Empty", "enrichment_attempted": True})
    await client.post("/companies/", json={"name": "Filled", "founded_year": 2010, "enrichment_attempted": True})
    long_ago = datetime.now(timezone.utc) - timedelta(days=8)
    for company in (await db_session.execute(select(Company))).scalars():
        company.enriched_at = long_ago
    await db_session.commit()
    assert [c["name"] for c in (await client.get("/companies/pending")).json()] == ["empty"]


async def _age(db_session, name: str, days: int):
    from datetime import datetime, timedelta, timezone
    from backend.models import Company
    company = (await db_session.execute(select(Company).where(Company.name == name))).scalar_one()
    company.enriched_at = datetime.now(timezone.utc) - timedelta(days=days)
    await db_session.commit()


async def test_pending_refreshes_year_old_data_by_newest_job_too(client, db_session):
    await _post_job(client, "New", 1)
    await _post_job(client, "Recent", 1)
    await _post_job(client, "Old", 1)  # stale data, but the newest job: first
    await client.post("/companies/", json={"name": "Old", "founded_year": 2010, "enrichment_attempted": True})
    await client.post("/companies/", json={"name": "Recent", "founded_year": 2011, "enrichment_attempted": True})
    await _age(db_session, "old", 400)
    await _age(db_session, "recent", 300)

    pending = (await client.get("/companies/pending")).json()
    # data under a year old is left alone
    assert [(c["name"], c["refresh"]) for c in pending] == [("old", True), ("new", False)]


async def test_refresh_replaces_stale_values_and_keeps_what_was_not_found(client, db_session):
    await client.post("/companies/", json={
        "name": "Synthesia", "industry": "chemical industry", "founded_year": 1920, "country": "Czechia",
        "enrichment_attempted": True,
    })
    await _age(db_session, "synthesia", 400)
    resp = await client.post("/companies/", json={
        "name": "Synthesia", "industry": "AI video", "founded_year": 2017, "enrichment_attempted": True,
    })
    body = resp.json()
    assert (body["industry"], body["founded_year"], body["country"]) == ("AI video", 2017, "Czechia")
    assert body["enrich_attempts"] == 1

    # fresh again: back to fill-empty
    resp = await client.post("/companies/", json={"name": "Synthesia", "industry": "x", "enrichment_attempted": True})
    assert resp.json()["industry"] == "AI video"


async def test_board_facts_never_replace_stale_values(client, db_session):
    await client.post("/companies/", json={"name": "Acme", "industry": "Software", "enrichment_attempted": True})
    await _age(db_session, "acme", 400)
    resp = await client.post("/companies/", json={"name": "Acme", "industry": "Other"})  # no enrichment_attempted
    assert resp.json()["industry"] == "Software"
