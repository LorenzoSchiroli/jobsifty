import uuid
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from backend.models import Outbox, Job, Company
from backend.outbox.worker import reconcile


async def _company(session, name="Acme") -> Company:
    c = Company(name=name)
    session.add(c)
    await session.flush()
    return c


async def _job(session, company_id, url="https://ex.com/1") -> Job:
    j = Job(url=url, title="Engineer", company_id=company_id,
            chunks=[{"text": "Engineer\npython", "embedding": [0.1] * 384}])
    session.add(j)
    await session.flush()
    return j


def _mget(found_ids):
    async def _call(index, body, _source):
        return {"docs": [{"_id": i, "found": i in found_ids} for i in body["ids"]]}
    return _call


async def test_reconcile_reindexes_changed_job_even_when_present(db_session):
    company = await _company(db_session)
    job = await _job(db_session, company.id)
    db_session.add(Outbox(event_type="job_upserted", entity_id=job.id))
    await db_session.commit()

    os_client = AsyncMock()
    os_client.mget.side_effect = _mget({str(job.id)})  # doc IS present -> only change-log catches it
    with patch("backend.outbox.worker.async_bulk", new_callable=AsyncMock) as mock_bulk:
        count = await reconcile(db_session, os_client)

    assert count == 1
    actions = list(mock_bulk.call_args.args[1])
    assert actions[0]["_id"] == str(job.id)
    assert actions[0]["_source"]["chunks"] == [{"text": "Engineer\npython", "embedding": [0.1] * 384}]
    assert "embedding" not in actions[0]["_source"]
    row = (await db_session.execute(select(Outbox))).scalar_one()
    assert row.processed_at is not None


async def test_reconcile_reindexes_missing_job_with_no_outbox_row(db_session):
    company = await _company(db_session, "MissingCo")
    job = await _job(db_session, company.id, "https://ex.com/missing")
    await db_session.commit()  # no outbox row: simulates a wiped index

    os_client = AsyncMock()
    os_client.mget.side_effect = _mget(set())  # doc absent
    with patch("backend.outbox.worker.async_bulk", new_callable=AsyncMock) as mock_bulk:
        count = await reconcile(db_session, os_client)

    assert count == 1
    actions = list(mock_bulk.call_args.args[1])
    assert actions[0]["_id"] == str(job.id)


async def test_reconcile_skips_present_unchanged_job(db_session):
    company = await _company(db_session, "PresentCo")
    job = await _job(db_session, company.id, "https://ex.com/present")
    await db_session.commit()

    os_client = AsyncMock()
    os_client.mget.side_effect = _mget({str(job.id)})  # present AND no outbox row
    with patch("backend.outbox.worker.async_bulk", new_callable=AsyncMock) as mock_bulk:
        count = await reconcile(db_session, os_client)

    assert count == 0
    mock_bulk.assert_not_called()


async def test_reconcile_company_event_runs_update_by_query(db_session):
    company = await _company(db_session, "PatchCo")
    db_session.add(Outbox(event_type="company_upserted", entity_id=company.id))
    await db_session.commit()

    os_client = AsyncMock()
    os_client.mget.side_effect = _mget(set())
    with patch("backend.outbox.worker.async_bulk", new_callable=AsyncMock):
        await reconcile(db_session, os_client)

    os_client.update_by_query.assert_called_once()
    row = (await db_session.execute(select(Outbox))).scalar_one()
    assert row.processed_at is not None


async def test_purge_expired_jobs_deletes_old_docs():
    from backend.outbox.worker import purge_expired_jobs
    os_client = AsyncMock()
    await purge_expired_jobs(os_client, max_age_days=30)
    os_client.delete_by_query.assert_called_once()
    body = os_client.delete_by_query.call_args.kwargs["body"]
    assert body["query"]["range"]["created_at"]["lt"] == "now-30d"
