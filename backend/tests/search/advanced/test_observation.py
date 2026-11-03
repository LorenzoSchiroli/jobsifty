from datetime import datetime, timezone

import pytest

from backend.search.advanced.observation import (
    Candidate,
    assign_short_ids,
    header_line,
    job_country,
    job_line,
    render_observation,
)
from backend.search.advanced.search import SearchVariant
from backend.search.filters import SearchFilters

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _cand(job_id, **kw) -> Candidate:
    base = dict(
        job_id=job_id, score=0.9, title="ML Engineer", company="Acme", location=None,
        job_country=None, company_country=None, location_type=None, seniority=None,
        employment_type=None, languages=[], salary=None, is_startup=None, is_consulting=None,
        posted_at="2026-11-01T12:00:00+00:00", text="", search_index=1, breaks=[],
    )
    return Candidate(**(base | kw))


@pytest.fixture
def sample_candidates():
    return [
        _cand("a1f3aaaa-0000", location_type="remote", location="Remote", seniority="senior"),
        _cand("b2c4bbbb-0000", location_type="remote", location="Berlin, Germany",
              job_country="Germany", salary="€85–100k", is_startup=True),
        _cand("c3d5cccc-0000", location_type="hybrid", languages=["en", "de"]),
        _cand("d4e6dddd-0000", location_type=None, posted_at="2026-10-20T12:00:00+00:00"),
    ]


@pytest.mark.parametrize("location,office,expected", [
    ("Berlin, Germany", None, "Germany"),
    ("Milan, IT", None, "Italy"),
    (None, "Remote, Netherlands", "Netherlands"),
    ("Anywhere", None, None),
])
def test_job_country(location, office, expected):
    assert job_country(location, office) == expected


def test_short_ids_are_unique_prefixes():
    ids = assign_short_ids(["a1f3aaaa-0000", "a1f3bbbb-0000"], {})
    assert sorted(ids) == ["a1f3a", "a1f3b"]


def test_short_ids_keep_existing():
    taken = {"a1f3": "a1f3aaaa-0000"}
    assert assign_short_ids(["a1f3aaaa-0000"], taken) == {}


def test_short_ids_do_not_reuse_a_taken_prefix():
    ids = assign_short_ids(["a1f3bbbb-0000"], {"a1f3": "a1f3aaaa-0000"})
    assert ids == {"a1f3b": "a1f3bbbb-0000"}


def test_observation_shows_counts_lines_and_pool(sample_candidates):
    obs = render_observation(1, SearchVariant(query="ml engineer"), None, sample_candidates,
                             assign_short_ids([c.job_id for c in sample_candidates], {}),
                             pool_size=41, now=NOW)
    assert obs.splitlines()[0].startswith('search 1 "ml engineer"')
    assert "remote 2" in obs and "job in Germany 1" in obs and "unknown 1" in obs
    assert obs.rstrip().endswith("pool: 41 distinct jobs")
    assert sum(1 for line in obs.splitlines() if line.lstrip().startswith("[")) == min(8, len(sample_candidates))
    assert header_line(obs) == obs.splitlines()[0]


def test_probe_header_and_breaks(sample_candidates):
    sample_candidates[0].breaks = ["remote only"]
    variant = SearchVariant(query="ml", probe="remote only")
    locked = SearchFilters(semantic_query="", max_age_hours=None, seniority="senior")
    obs = render_observation(2, variant, locked, sample_candidates,
                             assign_short_ids([c.job_id for c in sample_candidates], {}),
                             pool_size=4, now=NOW)
    assert '· probe: lifts "remote only"' in obs.splitlines()[0]
    assert "seniority=senior" in obs.splitlines()[0]
    assert "· breaks: remote only" in obs


def test_job_line_format_and_unknown_salary():
    c = _cand("a1f3aaaa-0000", title="Senior ML Engineer", location="Berlin, Germany",
              location_type="hybrid", seniority="senior", languages=["en"], is_startup=True)
    assert job_line(c, "a1f3", NOW) == (
        "[a1f3] Senior ML Engineer · Acme · Berlin · hybrid · senior · EN · — · startup · 2d · 0.90"
    )
