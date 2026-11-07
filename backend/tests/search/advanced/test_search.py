import pytest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Company, Job
from backend.search.advanced.brief import Brief
from backend.search.advanced.search import SearchVariant, VariantFilters, matches_soft, run_variant
from tests.search.advanced.conftest import NOW
from tests.search.advanced.samples import MILAN, REMOTE


def _hit(job_id, location_type="remote", **kw):
    src = {
        "job_id": str(job_id), "company_name": "Acme", "title": "ML Engineer",
        "chunks": [{"text": "first chunk"}, {"text": "the summary", "summary": True}],
        "employment_type": "full-time", "location_type": location_type, "seniority": "senior",
        "languages_required": ["english"], "is_consulting": False, "is_startup": True,
        "country": "DE", "job_cities": ["berlin"], "job_countries": ["Germany"],
        "posted_at": "2026-11-02T10:00:00+00:00",
    } | kw
    return {"_source": src}


async def _seed(engine) -> Job:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        company = Company(name="Acme")
        s.add(company)
        await s.flush()
        job = Job(url="http://x/1", company_id=company.id, title="ML Engineer",
                  location="Berlin, Germany", salary_range="€80k")
        s.add(job)
        await s.commit()
        return job


def _os(hits):
    client = MagicMock()
    client.search = AsyncMock(return_value={"hits": {"hits": hits}})
    return client


def _deps(engine, os_client):
    biencoder = MagicMock()
    biencoder.encode.return_value.tolist.return_value = [0.1, 0.2]
    reranker = MagicMock()
    reranker.predict.side_effect = lambda pairs: [1.0 - 0.1 * i for i in range(len(pairs))]
    return dict(biencoder=biencoder, reranker=reranker, os_client=os_client,
                session_factory=async_sessionmaker(engine, expire_on_commit=False))


async def test_run_variant_passes_locked_filters_as_hard(engine):
    job = await _seed(engine)
    os_client = _os([_hit(job.id)])
    await run_variant(variant=SearchVariant(query="ml"), brief=Brief(items=[REMOTE]),
                      search_index=1, **_deps(engine, os_client))
    body = os_client.search.call_args.kwargs["body"]
    assert {"term": {"location_type": "remote"}} in body["query"]["hybrid"]["queries"][0]["bool"]["filter"]
    assert body["size"] == 30


async def test_run_variant_loads_location_and_salary_from_postgres(engine):
    job = await _seed(engine)
    [c] = await run_variant(variant=SearchVariant(query="ml"), brief=Brief(), search_index=1,
                            **_deps(engine, _os([_hit(job.id)])))
    assert (c.location, c.salary) == ("Berlin, Germany", "€80k")
    assert (c.job_cities, c.job_countries) == (["berlin"], ["Germany"])  # where the job is: from the index
    assert c.text == "the summary"
    assert c.company_country == "DE" and c.fields(NOW)["company_country"] == "DE"
    assert c.score == 1.0 and c.search_index == 1


async def test_loosening_drops_a_hard_filter(engine):
    job = await _seed(engine)
    os_client = _os([_hit(job.id)])
    await run_variant(variant=SearchVariant(query="ml", loosen=[{"field": "location_type"}]),
                      brief=Brief(items=[REMOTE]), search_index=2, **_deps(engine, os_client))
    leg = os_client.search.call_args.kwargs["body"]["query"]["hybrid"]["queries"][0]["bool"]
    assert {"term": {"location_type": "remote"}} not in leg.get("filter", [])


async def test_loosening_searches_a_place_on_its_nearer_value(engine):
    job = await _seed(engine)
    os_client = _os([_hit(job.id)])
    variant = SearchVariant(query="ml", loosen=[{"field": "place", "value": ["Milano", "Schweiz", "Austria"]}])
    await run_variant(variant=variant, brief=Brief(items=[MILAN]), search_index=2, **_deps(engine, os_client))
    leg = os_client.search.call_args.kwargs["body"]["query"]["hybrid"]["queries"][0]["bool"]
    assert {"bool": {"should": [{"terms": {"job_cities": ["milan"]}},
                                {"terms": {"job_countries": ["Switzerland", "Austria"]}}],
                     "minimum_should_match": 1}} in leg["filter"]


def test_loosen_refuses_the_window_and_values_its_field_cannot_take():
    with pytest.raises(ValueError, match="max_age_hours"):
        SearchVariant(query="ml", loosen=[{"field": "posted_within_days", "value": 7}])
    with pytest.raises(ValueError, match="seniority cannot be"):
        SearchVariant(query="ml", loosen=[{"field": "seniority", "value": "wizard"}])


async def test_matches_soft(engine):
    job = await _seed(engine)
    [c] = await run_variant(variant=SearchVariant(query="ml"), brief=Brief(), search_index=1,
                            **_deps(engine, _os([_hit(job.id)])))
    soft = VariantFilters(seniority="senior", languages_required=["en"])
    assert matches_soft(c, SearchVariant(query="x", filters=soft))
    assert not matches_soft(c, SearchVariant(query="x", filters=VariantFilters(seniority="junior")))
    assert matches_soft(c, SearchVariant(query="x", filters=VariantFilters(company_country="DE")))
    assert matches_soft(c, SearchVariant(query="x", filters=VariantFilters(place=["München", "Berlin"])))
    assert not matches_soft(c, SearchVariant(query="x", filters=VariantFilters(place="Austria")))


async def test_list_valued_stated_item_is_a_hard_terms_clause(engine):
    from backend.search.advanced.brief import BriefItem

    job = await _seed(engine)
    os_client = _os([_hit(job.id, review_score=4.1)])
    item = BriefItem(text="remote or hybrid", kind="require", source="stated", field="location_type",
                     value="remote or hybrid")
    [c] = await run_variant(variant=SearchVariant(query="ml"), brief=Brief(items=[item]),
                            search_index=1, **_deps(engine, os_client))
    leg = os_client.search.call_args.kwargs["body"]["query"]["hybrid"]["queries"][0]["bool"]
    assert {"terms": {"location_type": ["remote", "hybrid"]}} in leg["filter"]
    assert c.review_score == 4.1


def test_variant_window_is_capped_at_the_live_window(monkeypatch):
    monkeypatch.setenv("JOB_LIVE_DAYS", "14")
    assert SearchVariant(query="q").max_age_hours is None  # the run's window, set by search_jobs
    assert SearchVariant(query="q", max_age_hours=1000).max_age_hours == 336
    assert SearchVariant(query="q", max_age_hours=168).max_age_hours == 168


async def test_closest_search_makes_the_stated_requirements_soft(engine):
    from backend.search.advanced.search import closest_search

    job = await _seed(engine)
    os_client = _os([_hit(job.id, location_type="on-site")])
    found = await closest_search(query="ml", brief=Brief(items=[REMOTE]), max_age_hours=168, **_deps(engine, os_client))
    lexical = os_client.search.call_args.kwargs["body"]["query"]["hybrid"]["queries"][0]["bool"]
    assert {"term": {"location_type": "remote"}} not in lexical["filter"]  # no hard filter: a near-miss surfaces
    assert "location_type" in str(lexical["should"]) and "posted_at" in str(lexical["filter"])  # soft; the window stays
    assert [(c.job_id, c.search_index) for c in found] == [(str(job.id), 0)]
