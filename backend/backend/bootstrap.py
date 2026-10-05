import asyncio
import logging
import os

from sqlalchemy import select
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from backend.database import get_session_factory
from backend.opensearch_client import (
    init_opensearch, get_opensearch, drop_legacy_index, INDEX_NAME, LEGACY_INDEX_NAME,
)
from backend.models import Job

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

logger = logging.getLogger(__name__)


async def backfill_created_at() -> None:
    factory = get_session_factory()
    os_client = get_opensearch()
    async with factory() as session:
        rows = (await session.execute(select(Job.id, Job.created_at))).all()
    if not rows:
        return
    body = []
    for job_id, created_at in rows:
        body.append({"update": {"_id": str(job_id)}})
        body.append({"doc": {"created_at": created_at.isoformat()}, "doc_as_upsert": False})
    await os_client.bulk(index=INDEX_NAME, body=body)
    logger.info("Backfilled created_at for %d jobs in OpenSearch", len(rows))


async def main() -> None:
    await init_opensearch()
    if await drop_legacy_index(get_opensearch()):
        logger.info("Dropped legacy OpenSearch index %r; reconcile repopulates %r", LEGACY_INDEX_NAME, INDEX_NAME)

    db_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

    await backfill_created_at()


if __name__ == "__main__":
    asyncio.run(main())
