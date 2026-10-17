import asyncio
import logging
import os

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from backend.opensearch_client import (
    init_opensearch, get_opensearch, drop_legacy_index, INDEX_NAME,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

logger = logging.getLogger(__name__)


async def main() -> None:
    await init_opensearch()
    if dropped := await drop_legacy_index(get_opensearch()):
        logger.info("Dropped legacy OpenSearch indexes %s; reconcile repopulates %r", dropped, INDEX_NAME)

    db_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()


if __name__ == "__main__":
    asyncio.run(main())
