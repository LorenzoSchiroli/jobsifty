"""Limits of the advanced-search agent run."""
from backend.live import job_live_days

MAX_TURNS = 6
MAX_SEARCHES = 9
MAX_QUESTIONS = 2
WALL_CLOCK_SECONDS = 240
FINALIZE_RESERVE_SECONDS = 45  # kept free for finalize: agent calls stop this long before the deadline
AGENT_MAX_TOKENS = 1500  # output tokens per agent call
FINALIZE_MAX_TOKENS = 3500  # output tokens of the finalize call: an entry for each of up to 12 jobs
DISTILL_SECONDS = 60  # the preference-memory call, after the run
MAX_VARIANTS = 3
CANDIDATE_WINDOW = 30
SHOWN_PER_SEARCH = 8
MAX_INSPECT = 3
INSPECT_CHARS = 600
FINALIZE_CANDIDATES = 12
FINALIZE_TEXT_CHARS = 300
MAX_SHORTLIST = 10  # jobs a result shows: the matches, then the closest jobs to fill it up
CV_CHARS = 3000
STALE_RUN_HOURS = 24
HEARTBEAT_SECONDS = 15
START_WINDOW_HOURS = 72  # a run's posting window when neither the user nor the memory states one


def live_window_hours() -> int:
    """The widest posting window a search can use: the index holds no job older than JOB_LIVE_DAYS."""
    return job_live_days() * 24
