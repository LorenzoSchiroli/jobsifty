import pytest


async def test_create_job_returns_201(client):
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/1",
        "title": "ML Engineer",
        "company_name": "Stripe",
        "seniority": "senior",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/job/1"
    assert data["title"] == "ML Engineer"
    assert data["seniority"] == "senior"
    assert "id" in data
    assert "company_id" in data


async def test_create_job_auto_creates_company_stub(client):
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/2",
        "title": "Data Scientist",
        "company_name": "NewCo",
    })
    assert resp.status_code == 201
    company_id = resp.json()["company_id"]
    company_resp = await client.get(f"/companies/{company_id}")
    assert company_resp.status_code == 200
    assert company_resp.json()["name"] == "newco"


async def test_create_job_links_to_existing_company(client):
    company_resp = await client.post("/companies/", json={"name": "Stripe", "industry": "fintech"})
    company_id = company_resp.json()["id"]

    job_resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/3",
        "title": "Engineer",
        "company_name": "Stripe",
    })
    assert job_resp.status_code == 201
    assert job_resp.json()["company_id"] == company_id


async def test_upsert_job_fills_null_fields(client):
    await client.post("/jobs/", json={
        "url": "https://example.com/job/4",
        "title": "ML Engineer",
        "company_name": "Stripe",
    })
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/4",
        "title": "ML Engineer",
        "company_name": "Stripe",
        "seniority": "senior",
        "location": "Berlin",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["seniority"] == "senior"
    assert data["location"] == "Berlin"


async def test_upsert_job_does_not_overwrite_existing_fields(client):
    await client.post("/jobs/", json={
        "url": "https://example.com/job/5",
        "title": "ML Engineer",
        "company_name": "Stripe",
        "seniority": "senior",
    })
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/5",
        "title": "ML Engineer",
        "company_name": "Stripe",
        "seniority": "junior",
    })
    assert resp.status_code == 200
    assert resp.json()["seniority"] == "senior"


async def test_get_job_returns_200(client):
    create_resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/6",
        "title": "Engineer",
        "company_name": "Stripe",
    })
    job_id = create_resp.json()["id"]
    resp = await client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


async def test_get_job_returns_404_when_not_found(client):
    resp = await client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_create_job_accepts_chunks(client, db_session):
    from sqlalchemy import select
    from backend.models import Job
    chunks = [{"text": "ML Engineer\nbuild models", "embedding": [0.1] * 384}]
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/job/schema-test",
        "title": "ML Engineer",
        "company_name": "Acme",
        "chunks": chunks,
    })
    assert resp.status_code == 201
    job = (await db_session.execute(select(Job).where(Job.url == "https://example.com/job/schema-test"))).scalar_one()
    assert job.chunks == chunks


from sqlalchemy import select
from backend.models import Outbox, Job


async def test_job_upsert_creates_outbox_event(client, db_session):
    await client.post("/jobs/", json={
        "url": "https://example.com/outbox-test",
        "title": "Engineer",
        "company_name": "Acme",
    })
    result = await db_session.execute(
        select(Outbox).where(Outbox.event_type == "job_upserted")
    )
    events = result.scalars().all()
    assert len(events) == 1


_CHUNKS_V1 = [{"text": "old chunk", "embedding": [0.1] * 384}]
_CHUNKS_V2 = [{"text": "new chunk", "embedding": [0.2] * 384}]


async def test_post_with_description_overwrites_derived_fields(client, db_session):
    from sqlalchemy import select
    from backend.models import Job
    await client.post("/jobs/", json={
        "url": "https://example.com/derive/1", "title": "ML Engineer", "company_name": "Stripe",
        "description": "Senior ML role in Berlin.", "seniority": "senior", "office": "berlin",
        "chunks": _CHUNKS_V1,
    })
    resp = await client.post("/jobs/", json={
        "url": "https://example.com/derive/1", "title": "ML Engineer", "company_name": "Stripe",
        "description": "Lead ML role, remote.", "location": "Berlin", "seniority": "lead", "office": None,
        "chunks": _CHUNKS_V2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["seniority"] == "lead"
    assert data["office"] is None
    assert data["location"] == "Berlin"
    job = (await db_session.execute(select(Job).where(Job.url == "https://example.com/derive/1"))).scalar_one()
    await db_session.refresh(job)
    assert job.chunks == _CHUNKS_V2


async def test_post_without_description_only_fills_empty_fields(client):
    await client.post("/jobs/", json={
        "url": "https://example.com/derive/2", "title": "ML Engineer", "company_name": "Stripe",
        "description": "Senior ML role.", "seniority": "senior", "chunks": _CHUNKS_V1,
    })
    for description in (None, "", "   "):
        resp = await client.post("/jobs/", json={
            "url": "https://example.com/derive/2", "title": "ML Engineer", "company_name": "Stripe",
            "description": description, "seniority": "junior", "employment_type": "full-time",
            "chunks": _CHUNKS_V2,
        })
        data = resp.json()
        assert data["seniority"] == "senior"
        assert data["employment_type"] == "full-time"
        assert data["description"] == "Senior ML role."


async def test_list_jobs_pages_by_id(client):
    for i in range(5):
        await client.post("/jobs/", json={
            "url": f"https://example.com/list/{i}", "title": f"Job {i}", "company_name": "Stripe",
            "description": f"desc {i}",
        })
    first = await client.get("/jobs/", params={"limit": 3})
    assert first.status_code == 200
    page1 = first.json()
    assert len(page1["items"]) == 3
    assert page1["next_after"] == page1["items"][-1]["id"]
    assert page1["items"][0]["company_name"] == "stripe"

    second = (await client.get("/jobs/", params={"limit": 3, "after": page1["next_after"]})).json()
    assert len(second["items"]) == 2
    assert second["next_after"] is None
    ids = [it["id"] for it in page1["items"] + second["items"]]
    assert ids == sorted(ids)
    assert len(set(ids)) == 5
    assert {it["description"] for it in page1["items"] + second["items"]} == {f"desc {i}" for i in range(5)}


async def test_list_jobs_rejects_limit_over_max(client):
    resp = await client.get("/jobs/", params={"limit": 5000})
    assert resp.status_code == 422
