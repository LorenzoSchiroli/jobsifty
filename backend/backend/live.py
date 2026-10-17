"""How long a job stays live: JOB_LIVE_DAYS (default 14) from its posting time, or from
when it was stored when the board gives none. Search index retention, reconcile, the
companies worth enriching and the URLs ingestion skips all follow it; ingestion reads
the same variable."""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.models import Job

DEFAULT_JOB_LIVE_DAYS = 14


def job_live_days() -> int:
    return int(os.environ.get("JOB_LIVE_DAYS") or DEFAULT_JOB_LIVE_DAYS)


def live_since(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=job_live_days())


# The date a job's age counts from, as a SQL expression (the search index's posted_at).
job_date = func.coalesce(Job.posted_at, Job.created_at)
