"""Candidates of an advanced-search run and the compact text the agent reads about a search."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backend.search.advanced.budget import CANDIDATE_WINDOW, SHOWN_PER_SEARCH
from backend.search.advanced.vocabulary import LANGUAGE_CODES
from backend.search.countries import canonical_country
from backend.search.filters import SearchFilters

if TYPE_CHECKING:
    from backend.search.advanced.search import SearchVariant

@dataclass
class Candidate:
    job_id: str
    score: float
    title: str
    company: str | None
    location: str | None
    job_country: str | None
    company_country: str | None
    location_type: str | None
    seniority: str | None
    employment_type: str | None
    languages: list[str]
    salary: str | None
    is_startup: bool | None
    is_consulting: bool | None
    posted_at: str
    text: str
    search_index: int
    breaks: list[str] = field(default_factory=list)  # texts of the stated items a probe job breaks
    review_score: float | None = None

    def fields(self, now: datetime) -> dict:
        """The view the brief's rules read, keyed by brief field; the job's age in days as of `now`."""
        hours = _age_hours(self.posted_at, now)
        return {
            "location_type": self.location_type,
            "seniority": self.seniority,
            "employment_type": self.employment_type,
            "languages_required": self.languages,
            "is_consulting": self.is_consulting,
            "is_startup": self.is_startup,
            "company_country": self.company_country,
            "min_review_score": self.review_score,
            "posted_within_days": None if hours is None else hours / 24,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Candidate:
        return cls(**data)


def job_country(location: str | None, office: str | None) -> str | None:
    """The country at the end of the job's location, else of its office."""
    for text in (location, office):
        if text:
            if country := canonical_country(text.split(",")[-1], in_location=True):
                return country
    return None


def assign_short_ids(job_ids: list[str], taken: dict[str, str]) -> dict[str, str]:
    """New short -> job_id entries: the shortest prefix (4+ hex chars) no other id shares."""
    already = {job_id: short for short, job_id in taken.items()}
    hexes = {job_id: job_id.replace("-", "").lower() for job_id in {*job_ids, *taken.values()}}
    new: dict[str, str] = {}
    for job_id in job_ids:
        if job_id in already or job_id in new.values():
            continue
        h = hexes[job_id]
        others = [o for j, o in hexes.items() if j != job_id]
        length = min(4, len(h))
        while length < len(h) and any(o.startswith(h[:length]) for o in others):
            length += 1
        new[h[:length]] = job_id
    return new


def _age_hours(posted_at: str | None, now: datetime) -> int | None:
    """Hours since posting; None when the timestamp is missing or unparseable. Naive means UTC."""
    try:
        posted = datetime.fromisoformat(posted_at)
    except (TypeError, ValueError):
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return max(0, int((now - posted).total_seconds() // 3600))


def _age(posted_at: str | None, now: datetime) -> str | None:
    hours = _age_hours(posted_at, now)
    if hours is None:
        return None
    return f"{hours}h" if hours < 24 else f"{hours // 24}d"


def _language_code(name: str) -> str:
    return LANGUAGE_CODES.get(name.casefold(), name).upper()


def job_line(c: Candidate, short_id: str, now: datetime) -> str:
    parts = [c.title, c.company, c.location.split(",")[0].strip() if c.location else None,
             c.location_type, c.seniority, "/".join(_language_code(l) for l in c.languages) or None,
             c.salary or "—"]
    if c.is_startup:
        parts.append("startup")
    if c.is_consulting:
        parts.append("consulting")
    parts += [_age(c.posted_at, now), f"{c.score:.2f}"]  # unknown age is left out
    line = f"[{short_id}] " + " · ".join(p for p in parts if p)
    if c.breaks:
        line += " · breaks: " + ", ".join(c.breaks)
    return line


def _describe(filters: SearchFilters | None, skip: tuple[str, ...] = ()) -> str:
    if filters is None:
        return "none"
    items = [f"{'company_country' if k == 'country' else k}={'/'.join(v) if isinstance(v, list) else v}"
             for k, v in filters.model_dump(exclude={"semantic_query", "strict", "max_age_hours", *skip}).items()
             if v is not None]
    return ", ".join(items) or "none"


def window_text(hours: int | None) -> str:
    if hours is None:
        return "any time"
    return f"{hours}h" if hours < 24 else f"{hours // 24}d"


# Why a search ran on another window than its variant asked, by the rule that cut it (tools._window).
WINDOW_REASONS = {
    "ladder": "it widens one step per search",
    "stated": "the user's stated window; only the user can change it",
    "probe": "a probe runs on the run's window and lifts only its item",
    "window_probe": "a probe of the stated window looks one step wider",
}


def window_note(asked: int | None, used: int | None, reason: str) -> str | None:
    """The observation line saying why a search ran on another window; None when it ran on the one asked."""
    if asked == used:
        return None
    return f"window: last {window_text(used)} (asked {window_text(asked)}); {WINDOW_REASONS[reason]}"


def header_line(observation: str) -> str:
    return observation.splitlines()[0]


def _counts(label: str, values: list) -> str:
    counter = Counter("unknown" if v is None else str(v) for v in values)
    ordered = sorted(counter.items(), key=lambda kv: (kv[0] == "unknown", -kv[1], kv[0]))
    return f"  {label}: " + " · ".join(f"{k} {n}" for k, n in ordered)


def _yes_no(values: list[bool | None]) -> str:
    c = Counter(values)
    return f"yes {c[True]}, no {c[False]}, unknown {c[None]}"


def render_observation(
    index: int,
    variant: SearchVariant,
    locked: SearchFilters | None,
    candidates: list[Candidate],
    short_ids: dict[str, str],
    pool_size: int,
    now: datetime,
    note: str | None = None,
) -> str:
    from backend.search.advanced.search import soft_matches

    soft = _describe(variant.filters.to_search_filters(variant.query, variant.max_age_hours))
    header = (f'search {index} "{variant.query}" · locked: {_describe(locked)} · soft: {soft}'
              f" · last {window_text(variant.max_age_hours)}")
    if variant.probe:
        header += f' · probe: lifts "{variant.probe}"'
    window = candidates[:CANDIDATE_WINDOW]
    hours = [_age_hours(c.posted_at, now) for c in window]
    ages = [h // 24 for h in hours if h is not None]
    langs = [_language_code(l) for c in window for l in c.languages if l.casefold() != "english"]
    lines = [
        header,
        *([f"  {note}"] if note else []),
        f"over the top {len(window)}:",
        _counts("location type", [c.location_type for c in window]),
        _counts("job in", [c.job_country for c in window]),
        _counts("company based", [canonical_country(c.company_country) or c.company_country for c in window]),
        _counts("seniority", [c.seniority for c in window]),
        "  required languages other than English: " + (", ".join(f"{k} {n}" for k, n in Counter(langs).most_common()) or "none"),
        f"  startup: {_yes_no([c.is_startup for c in window])}; consulting: {_yes_no([c.is_consulting for c in window])}",
        f"  posted: ≤3d {sum(a <= 3 for a in ages)}, 4–7d {sum(3 < a <= 7 for a in ages)}, older {sum(a > 7 for a in ages)}, age unknown {hours.count(None)}",
        f"  salary known: {sum(1 for c in window if c.salary)}",
        (f"matching all soft filters: {matched}" if (matched := soft_matches(variant, window)) is not None
         else f"no soft filters: {len(window)} jobs returned"),
    ]
    by_job = {job_id: short for short, job_id in short_ids.items()}
    for c in window[:SHOWN_PER_SEARCH]:
        lines.append("  " + job_line(c, by_job.get(c.job_id, c.job_id[:4]), now))
    lines.append(f"pool: {pool_size} distinct jobs")
    return "\n".join(lines)
