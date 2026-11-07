"""Progress lines for slow requests: a client that sends `Accept: application/x-ndjson` gets one
JSON object per line, `{"status": "..."}` for each step, then `{"result": ...}` or `{"error": ...}`.
Other clients get the plain JSON answer, so the same endpoint serves both."""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

NDJSON = "application/x-ndjson"

Report = Callable[[str], None]


def ignore(_: str) -> None:
    """The report of a request that does not stream."""


def wants_progress(request: Request) -> bool:
    return NDJSON in request.headers.get("accept", "")


def _line(obj: dict) -> str:
    return json.dumps(jsonable_encoder(obj)) + "\n"


def stream_progress(work: Callable[[Report], Awaitable[object]]) -> StreamingResponse:
    """Runs `work(report)` and streams each reported status, then its result."""
    async def lines():
        queue: asyncio.Queue[str] = asyncio.Queue()
        task = asyncio.create_task(work(queue.put_nowait))
        try:
            while not task.done() or not queue.empty():
                getter = asyncio.create_task(queue.get())
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    yield _line({"status": getter.result()})
                else:
                    getter.cancel()
            try:
                yield _line({"result": task.result()})
            except HTTPException as e:
                yield _line({"error": e.detail})
            except Exception:
                logger.exception("streamed request failed")
                yield _line({"error": "Search failed"})
        finally:
            task.cancel()  # the client went away: stop the work

    return StreamingResponse(lines(), media_type=NDJSON,
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
