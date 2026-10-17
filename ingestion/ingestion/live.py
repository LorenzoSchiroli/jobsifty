import os

# How long a job stays live, counted from its posting time (the time it was stored when
# the board gives none). The backend reads the same JOB_LIVE_DAYS: search index retention,
# reconcile and which companies are worth enriching all follow it.
DEFAULT_JOB_LIVE_DAYS = 14


def job_live_days() -> int:
    return int(os.environ.get("JOB_LIVE_DAYS") or DEFAULT_JOB_LIVE_DAYS)
