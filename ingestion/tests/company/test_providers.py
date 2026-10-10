from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion.company.scraping.providers import Exa, Hit, SearchPool, Tavily, TinyFish

_REQ = "ingestion.company.scraping.providers.requests"


@pytest.fixture(autouse=True)
def _no_spacing(monkeypatch):
    monkeypatch.setattr(TinyFish, "min_interval", 0.0)


def _response(status: int = 200, body: dict | None = None, headers: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status, text=str(body), headers=headers or {})
    resp.json.return_value = body or {}
    return resp


def test_tinyfish_request_and_parse():
    body = {"results": [{"url": "https://acme.com", "snippet": "Acme makes rockets", "title": "Acme"},
                        {"url": "https://b.com", "title": "B only title"}]}
    with patch(f"{_REQ}.get", return_value=_response(body=body)) as get:
        result = TinyFish("k").search("acme", 1, ["glassdoor.com", "glassdoor.de"])
    assert get.call_args.kwargs["params"] == {"query": "acme", "include_domains": "glassdoor.com,glassdoor.de"}
    assert get.call_args.kwargs["headers"] == {"X-API-Key": "k"}
    assert result.provider == "tinyfish" and result.hits == [Hit("https://acme.com", "Acme makes rockets")]
    assert result.answer is None


def test_exa_request_and_parse():
    body = {"results": [{"url": "https://acme.com", "highlights": ["Founded 2010.", "500 staff."]}]}
    with patch(f"{_REQ}.post", return_value=_response(body=body)) as post:
        result = Exa("k").search("acme", 5, ["glassdoor.com"])
    sent = post.call_args.kwargs["json"]
    assert sent["numResults"] == 5 and sent["includeDomains"] == ["glassdoor.com"]
    assert result.hits == [Hit("https://acme.com", "Founded 2010. 500 staff.")]


def test_tavily_asks_for_an_answer_only_when_wanted():
    body = {"answer": "Acme is a rocket maker.", "results": [{"url": "https://acme.com", "content": "c"}]}
    with patch(f"{_REQ}.post", return_value=_response(body=body)) as post:
        result = Tavily("k").search("acme", 5, answer=True)
        Tavily("k").search("acme", 5)
    assert post.call_args_list[0].kwargs["json"]["include_answer"] == "basic"
    assert "include_answer" not in post.call_args_list[1].kwargs["json"]
    assert result.answer == "Acme is a rocket maker."


def _pool(*providers):
    return SearchPool(list(providers))


def test_first_usable_provider_answers():
    first, second = TinyFish("k"), Exa("k")
    with patch(f"{_REQ}.get", return_value=_response(body={"results": [{"url": "https://a.com"}]})), \
         patch(f"{_REQ}.post") as post:
        result = _pool(first, second).search("q")
    post.assert_not_called()
    assert result.provider == "tinyfish" and first.ok == 1


def test_rate_limit_pauses_provider_and_falls_through():
    first, second = TinyFish("k"), Exa("k")
    pool = _pool(first, second)
    with patch(f"{_REQ}.get", return_value=_response(429, headers={"Retry-After": "120"})) as get, \
         patch(f"{_REQ}.post", return_value=_response(body={"results": [{"url": "https://a.com"}]})):
        assert pool.search("q").provider == "exa"
        assert pool.search("q").provider == "exa"
    assert get.call_count == 1  # paused, not retried
    assert not first.usable() and not first.disabled
    assert first.paused_until - __import__("time").monotonic() > 100


@pytest.mark.parametrize("provider_cls, status", [(TinyFish, 402), (Exa, 402), (Tavily, 432), (Tavily, 401)])
def test_out_of_credits_or_bad_key_disables_for_the_run(provider_cls, status):
    provider = provider_cls("k")
    pool = _pool(provider)
    with patch(f"{_REQ}.get", return_value=_response(status)), patch(f"{_REQ}.post", return_value=_response(status)):
        assert pool.search("q") is None
        assert pool.search("q") is None
    assert provider.disabled and provider.calls == 1
    assert pool.down()


def test_transient_errors_move_on_and_pause_after_a_streak():
    first, second = TinyFish("k"), Exa("k")
    pool = _pool(first, second)
    with patch(f"{_REQ}.get", side_effect=requests.ConnectionError("reset")) as get, \
         patch(f"{_REQ}.post", return_value=_response(body={"results": []})):
        for _ in range(5):
            assert pool.search("q").provider == "exa"
    assert get.call_count == 3  # paused after three failures in a row
    assert not first.disabled and not pool.down()


def test_unexpected_body_counts_as_transient():
    provider = TinyFish("k")
    with patch(f"{_REQ}.get", return_value=_response(body={"results": [{"no_url": 1}, "junk"]})):
        assert _pool(provider).search("q") is None
    assert provider.failed == 1 and not provider.disabled


def test_providers_without_a_key_are_skipped(monkeypatch):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_PROVIDERS", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    pool = SearchPool.from_env()
    assert [p.name for p in pool.providers] == ["tinyfish", "exa", "tavily"]
    assert pool.configured() == ["exa", "tavily"]
    with patch(f"{_REQ}.get") as get, patch(f"{_REQ}.post", return_value=_response(body={"results": []})):
        assert pool.search("q").provider == "exa"
    get.assert_not_called()
    assert pool.stats() == "tinyfish no key, exa 1 ok, tavily 0 ok"


def test_order_from_env(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDERS", "tavily, bogus ,exa")
    assert [p.name for p in SearchPool.from_env().providers] == ["tavily", "exa"]


def test_no_keys_is_not_down(monkeypatch):
    for key in ("TINYFISH_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "SEARCH_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)
    pool = SearchPool.from_env()
    assert pool.configured() == [] and not pool.down()
    assert pool.search("q") is None
