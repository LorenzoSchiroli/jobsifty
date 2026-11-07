import json

import pytest
from fastapi import HTTPException

from backend.progress import stream_progress

pytestmark = pytest.mark.asyncio


async def _lines(work) -> list[dict]:
    response = stream_progress(work)
    return [json.loads(chunk) async for chunk in response.body_iterator]


async def test_streams_each_status_then_the_result():
    async def work(report):
        report("Searching jobs")
        report("Ranking 3 matches")
        return [{"id": 1}]

    assert await _lines(work) == [{"status": "Searching jobs"}, {"status": "Ranking 3 matches"},
                                  {"result": [{"id": 1}]}]


async def test_an_http_error_becomes_an_error_line():
    async def work(report):
        report("Searching jobs")
        raise HTTPException(status_code=404, detail="Search not found.")

    assert await _lines(work) == [{"status": "Searching jobs"}, {"error": "Search not found."}]


async def test_an_unexpected_error_is_not_leaked():
    async def work(report):
        raise RuntimeError("secret detail")

    assert await _lines(work) == [{"error": "Search failed"}]
