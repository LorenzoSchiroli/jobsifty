"""Limits of the advanced-search agent run."""
from backend.live import job_live_days

MAX_TURNS = 6
MAX_SEARCHES = 9
MAX_PROBES = 2
MAX_QUESTIONS = 2
WALL_CLOCK_SECONDS = 240
FINALIZE_RESERVE_SECONDS = 45  # kept free for finalize: agent calls stop this long before the deadline
AGENT_MAX_TOKENS = 1500  # output tokens per agent call
FINALIZE_MAX_TOKENS = 2500  # output tokens of the finalize call
DISTILL_SECONDS = 60  # the preference-memory call, after the run
MAX_VARIANTS = 3
CANDIDATE_WINDOW = 30
SHOWN_PER_SEARCH = 8
MAX_INSPECT = 3
INSPECT_CHARS = 600
FINALIZE_CANDIDATES = 12
FINALIZE_TEXT_CHARS = 300
MAX_SHORTLIST = 10
CV_CHARS = 3000
STALE_RUN_HOURS = 24
HEARTBEAT_SECONDS = 15


# The posting windows a run widens through, in hours: 3 days, 7 days, then the live window. A search
# may go one step above the widest window used so far (tools.search_jobs enforces it).
WINDOW_STEPS_HOURS = (72, 168)


def window_steps() -> tuple[int, ...]:
    """The ladder of posting windows in hours, ending at JOB_LIVE_DAYS (the index holds nothing older)."""
    cap = job_live_days() * 24
    return tuple(sorted({min(h, cap) for h in WINDOW_STEPS_HOURS} | {cap}))


def window_limit(widest: int) -> int:
    """The widest window the next search may use: the first step above `widest` (the last step at most)."""
    steps = window_steps()
    return next((s for s in steps if s > widest), steps[-1])
