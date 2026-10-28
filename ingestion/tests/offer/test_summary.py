import json
from unittest.mock import MagicMock

import pytest

from ingestion.offer.parsing.summary import _parse, summarize
from ingestion.offer.scraping.models import JobOffer


def _offer(description: str | None = "<p>Train <b>ranking</b> models.</p>") -> JobOffer:
    return JobOffer(title="ML Engineer", company="Acme", location="Berlin", url="https://x/1",
                    source="test", posted_at=None, description=description)


def _pool(answer: str | None = "python") -> MagicMock:
    pool = MagicMock()
    pool.configured.return_value = ["nvidia"]
    pool.down.return_value = False
    pool.complete.return_value = answer
    return pool


def test_parse_joins_the_lists_in_order():
    content = json.dumps({"role_info": ["remote"], "requirements": ["python", " "], "responsibilities": ["ranking"],
                          "domain": None})
    assert _parse(content) == "remote python ranking"


def test_parse_strips_a_markdown_fence():
    assert _parse('```json\n{"requirements": ["sql"]}\n```') == "sql"


def test_parse_unwraps_an_object_in_a_list():
    assert _parse('[{"requirements": ["sql"]}]') == "sql"


def test_parse_rejects_an_empty_summary():
    # an empty answer counts as that provider's failure, so the pool tries the next one
    with pytest.raises(ValueError):
        _parse('{"role_info": [], "requirements": []}')


def test_summarize_sends_the_cleaned_description():
    pool = _pool()
    assert summarize(_offer(), pool) == "python"
    prompt = pool.complete.call_args.args[0]
    assert "Job title: ML Engineer" in prompt and "Train ranking models." in prompt and "<b>" not in prompt


@pytest.mark.parametrize("pool", [None, "down", "unconfigured"])
def test_summarize_without_a_working_pool_is_none(pool):
    if pool == "down":
        pool = _pool(); pool.down.return_value = True
    elif pool == "unconfigured":
        pool = _pool(); pool.configured.return_value = []
    assert summarize(_offer(), pool) is None
    if pool is not None:
        pool.complete.assert_not_called()


def test_summarize_skips_an_offer_without_description():
    pool = _pool()
    assert summarize(_offer(None), pool) is None
    pool.complete.assert_not_called()
