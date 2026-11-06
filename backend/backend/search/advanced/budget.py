"""Limits of the advanced-search agent run."""

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
