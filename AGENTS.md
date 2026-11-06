# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

**Jobsifty** is a job search ranking system. It scrapes job offers from multiple sources, enriches them with rule-based metadata, chunked multilingual embeddings and an optional LLM summary chunk, stores them in Postgres + OpenSearch, and serves a hybrid BM25 + k-NN search endpoint with cross-encoder reranking.

## Repository Structure

This is a `uv` workspace with three packages:

- **`backend/`** — FastAPI service: REST API (JWT auth), Postgres (SQLAlchemy async), OpenSearch hybrid search, outbox reconcile worker, LangGraph agent (advanced search)
- **`ingestion/`** — Pipeline: job scraping, deterministic offer parsing, an optional LLM summary per offer, company enrichment (LLM only for judgement fields), chunked embedding, and HTTP posting to backend
- **`llmpool/`** — the provider pool (the base provider pool and the OpenAI-compatible LLM chat providers: `llmpool/pool.py`, `llmpool/llm.py`; the web-search providers stay in `ingestion/company/scraping/providers.py`), used by `ingestion/` (`backend/` declares it, but the advanced-search agent calls its LLM through `ChatOpenAI`, not the pool)

Other top-level directories:
- `frontend/` — React (Vite) SPA, served by nginx in Docker/k8s
- `deploy/` — Helm chart (`deploy/helm/jobsifty/`) + k8s runbook (`deploy/k8s/README.md`); **AWS showcase** is the ECS managed path in `deploy/infra/aws/` (OpenTofu + Fargate/RDS/OpenSearch Service — not Helm on AWS)
- `dumps/` — `jobsifty.current.dump` is the demo database every target restores from / captures into (gitignored; seed with `deploy/scripts/seed-dump`)
- `tailor/` — standalone CV-tailoring CLI scripts (gitignored, local docx/cover-letter scripts; **not** imported by `backend/` or `ingestion/`, do not wire new code to it)

The autofill feature (Chrome extension + tailorer LangGraph agent that drafted CVs/cover letters and filled application forms) lives on the `autofill` branch, not on `main`.

[`README.md`](./README.md) is the human-facing counterpart: same architecture, with diagrams and the reasoning behind each choice. Keep the two consistent when behaviour changes.

Ad-hoc CLIs (run from `ingestion/`):
- `uv run python -m ingestion.offer ai` — offer scraping for a scope
- `uv run python -m ingestion.company "<name>"` — company enrichment

## Commands

### Infrastructure

Kubernetes (Helm, kind cluster locally) is the canonical deployment; docker-compose still works but is being phased out.

`deploy/scripts/run` is the single entrypoint for every deploy target. The
target is always explicit, so each stack configures itself — `local` is kind +
Helm on this machine, `hetzner` is OpenTofu + Helm, `aws` is OpenTofu + ECS
Fargate. Targets are independent and the script does **not** check whether
another one is live; bring the previous one down first.

```bash
deploy/scripts/run local                   # kind + Helm here → restore dump (up is the default action)
deploy/scripts/run hetzner up              # tofu apply → helm → restore dump, DNS follows
deploy/scripts/run aws up --no-dns         # tofu apply → ECS, domain left where it is
deploy/scripts/run aws down --yes          # dump → promote → tofu destroy, no prompt
```

Every target — `local` included — restores from / captures into
`dumps/jobsifty.current.dump` (seed it with `deploy/scripts/seed-dump`), so a
kind cluster serves the same demo data as the cloud stacks. `up` restores it
and `down` promotes what the stack held, which means a local `down` overwrites
the canonical dump the next cloud deploy will restore.

DNS follows the deploy: bringing a
cloud target up points Cloudflare `app`/`api`/apex/`www` at it, and taking it
down removes those records, so the target that is up owns the domain. `run`
passes `manage_dns` to tofu as a `-var`, so the value in each tfvars is not
consulted — pass `--no-dns` to bring a stack up without claiming DNS (reach it
via the ALB hostname instead). Bring the previous target down first; otherwise
the second apply fails on a Cloudflare record conflict.

```bash
docker compose up -d postgres opensearch   # start dependencies only
docker compose up --build                  # full stack (postgres + opensearch + backend + ingestion + frontend)

# Full runbook (cluster/images/secret setup) in deploy/k8s/README.md
helm install jobsifty deploy/helm/jobsifty -f deploy/helm/jobsifty/values-local.yaml
```

The chart deploys the whole stack: postgres, opensearch, api (+ HPA), worker, ingestion CronJob, frontend, and a bootstrap hook Job (migrations + OpenSearch index + checkpointer setup). It references a pre-created `jobsifty-secrets` secret. `deploy/k8s/loadtest-job.yaml` is an in-cluster k6 load-test Job for the HPA demo.

### Hetzner image gate (x86)

Before deploying the Hetzner profile, publish backend, ingestion, and frontend
images for `linux/amd64` via GitHub Actions (**Build and push images**
workflow; see `deploy/k8s/README.md`). Set `VITE_API_URL` in `.env.public`
first. Laptop `docker buildx build --platform linux/amd64` remains a fallback.
The backend image includes `pg_dump` and `rclone` for the worker's nightly
Postgres backup loop. The ingestion image is the highest-risk component because
it includes Playwright and python-jobspy/curl_cffi. Nodes are CX33 (x86);
do not pull arm64-only images onto them.

### AWS ECS specifics

Full runbook in `deploy/infra/aws/README.md`. Things that differ from the Helm path:

- **Migrations run as a one-shot RunTask, not a hook Job.** `deploy/scripts/run aws up` fires it (`aws_run_bootstrap_task`) between apply and dump restore; a bare `tofu apply` does not, so after a manual apply you must `aws ecs run-task` the `bootstrap` task definition yourself. A green ALB `/health` does **not** mean the DB is migrated.
- **Ingestion is an EventBridge Scheduler `RunTask`** (every 2h), and it posts to the internal Cloud Map address `http://api.<project>.local:8000` — never the public hostname, so a run cannot write into whichever stack currently owns DNS.
- **Secrets** live in one Secrets Manager entry; each key is injected per container via `secrets[].valueFrom` (`local.app_secret_keys` in `ecs.tf`; ingestion gets only `local.ingestion_secret_keys`: no database, OpenSearch or JWT secrets). Adding a secret means adding it there. **Non-secrets** come from the repo's `.env.public`, which tofu reads and passes as plain `environment` (only `CORS_ORIGINS` follows the stack's domain), so the AWS stack picks up an `.env.public` edit on the next apply.
- **OpenSearch** is VPC-only with fine-grained access control; the backend authenticates as the internal-database master user over basic auth, so the domain policy is deliberately principal-`*` (an IAM-scoped policy 403s basic auth before FGAC sees it). Access is closed by the security group instead.
- **The demo dump rides in S3** (`dump.tf`, `pgtools` image): `run aws up` restores it, `run aws down` captures and promotes it before destroying. A 4th image, `jobsifty-pgtools`, must be published alongside the other three.
- **Cost guards are part of the stack**: AWS Budgets (80%/100% email), 7-day CloudWatch retention, 7-day S3 dump lifecycle, single NAT, single-AZ RDS, one-node OpenSearch. Do not "fix" these silently — they are deliberate demo-scale choices.
- **Deployer IAM**: `deployer-policy.example.json` is committed, the filled-in `deployer-policy.json` (real account ID) is gitignored. New AWS resources usually mean new actions in the example policy.

### Backend

```bash
cd backend
uv run alembic upgrade head                # apply DB migrations
uv run uvicorn backend.main:app --reload   # run dev server on :8000
uv run pytest                              # run all backend tests
uv run pytest tests/test_jobs.py           # run a single test file
uv run pytest tests/search/test_filters.py::test_build_clauses_empty_when_all_none  # single test
```

Tests require a live Postgres at `postgresql+asyncpg://postgres:postgres@localhost:5432/jobsifty_test` (override via `TEST_DATABASE_URL`). OpenSearch and ML models are mocked in the test suite.

### Ingestion

```bash
cd ingestion
uv run python -m ingestion.pipeline ai --hours 48
uv run pytest                              # run all ingestion tests
```

### Required Environment Variables

**Secrets** — create a local `.env` from `.env.example` (gitignored):

```
GROQ_API_KEY=...                        # LLM: advanced-search agent (ChatOpenAI), company enrichment
NVIDIA_NIM_API_KEY=...                  # LLM pools: offer summaries, company enrichment
GEMINI_API_KEY=...                      #   free tier is used by Google for training; every provider is assumed to keep prompts, so the CV is cleaned before it is saved (/cv page)
SECRET_KEY=...                          # JWT signing
INGEST_TOKEN=...                        # ingestion's writes (X-Ingest-Token on POST /jobs/, /companies/)
TINYFISH_API_KEY=...                    # company web search pool (any subset): TinyFish free, rate limited
EXA_API_KEY=...                         #   Exa $10/month free credits
TAVILY_API_KEY=...                      #   Tavily 1,000 searches/month free
ADZUNA_APP_ID=...                       # optional job source
ADZUNA_APP_KEY=...
BACKUP_SBOX_HOST=...                    # optional; with USER + rclone pass enables backups
BACKUP_SBOX_USER=...
# BACKUP_SBOX_RCLONE_PASS=...           # output of rclone obscure
```

**Non-secrets** — committed in `.env.public` (edit in git):

```
GROQ_MODEL_LARGE=...                    # advanced search
ADVANCED_LLM_BASE_URL / _MODEL          # optional: advanced search on another OpenAI-compatible endpoint (default: Groq settings)
GROQ_MODEL_BASE=...                     # company enrichment (never offer summaries)
NVIDIA_MODEL=... / GEMINI_MODEL=...     # models of the other providers; comma-separated: a model answering 404 gives way to the next
LLM_PROVIDERS=nvidia,gemini,groq        # company enrichment LLM pool order (Groq last, to keep its daily tokens)
OFFER_LLM_PROVIDERS=nvidia,gemini       # offer summary LLM pool order (Groq left out)
JOB_LIVE_DAYS=14                        # days a job stays live (from posted_at, else created_at)
SEARCH_PROVIDERS=tinyfish,exa,tavily    # web search pool order
ACCESS_TOKEN_EXPIRE_DAYS=7
CORS_ORIGINS=...                        # extra API browser origins (optional)
BACKUP_SBOX_PATH=backups/jobsifty
VITE_API_URL=https://api.example.com    # baked into frontend GHCR images
```

Keep values UNQUOTED: `kubectl create secret --from-env-file` stores quotes verbatim (unlike dotenv), so a quoted `GROQ_API_KEY` yields Groq `401 invalid_api_key` in k8s. Create the cluster Secret from both files (`.env.public` then `.env`).

## Architecture

### Search Pipeline (backend)

`POST /jobs/search` (JWT-protected, body `{query}`) runs four sequential steps —
**no LLM involved**:

1. **Query parsing** — deterministic regex parsing extracts `SearchFilters`
   (seniority, location_type, languages, etc.) and a `semantic_query` from the
   query text (`search/query_parsing.py`). The advanced-search agent
   (`POST /jobs/search/advanced`, below) has its own LLM-driven path.
2. **Hybrid retrieval** — OpenSearch hybrid query: BM25 on `description` + nested k-NN on `chunks.embedding` (384-dim, `intfloat/multilingual-e5-small`, query encoded with a `query: ` prefix via `encode_query()`; a job scores by its best chunk via `score_mode: max`; hybrid queries drop `inner_hits`, so hits return the chunk vectors in `_source`), results combined via min-max normalization with 50/50 weights (`search/retrieval.py`). Filter clauses are soft by default (scored `should` with boost=2) so near-misses still surface; words like "strictly" / "exactly" / "no exceptions" in the query set `SearchFilters.strict`, applying them as a hard `post_filter` with a larger prefetch (200). The age window is always hard: jobs posted within `max_age_hours` (default 72; the query widens or narrows it with "last 2 weeks", "today", "this week"), on the doc's `posted_at` (the job's `posted_at`, else `created_at`). On the k-NN leg it is the knn query's own `filter` on `chunks.posted_at`, so the k nearest chunks are found among recent jobs rather than filtered afterwards.
3. **Cross-encoder reranking** — `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (override with `RERANKER_MODEL`, `max_length=256`) reranks retrieved hits on the job's LLM summary chunk, else the chunk closest to the query embedding, picked in Python from `_source.chunks` (title if a job has no chunks), returns top 20; torch threads follow `TORCH_NUM_THREADS` (default 2, matching the api pod's 2-CPU limit) (`search/reranker.py`)
4. **Postgres round-trip** — full `Job` + `Company` records fetched by ID for the response

**Auth**: `backend/auth/` + `routers/auth.py` — JWT bearer tokens (`POST /auth/register`, `POST /auth/login`, `GET /auth/me`); most endpoints depend on `get_current_user`. Ingestion's writes (`POST /jobs/`, `POST /companies/`) instead need the shared `INGEST_TOKEN` in an `X-Ingest-Token` header (`require_ingest_token`), and are refused while it is unset; AWS generates it, elsewhere it goes in `.env`.

**Advanced search (WIP)** — `POST /jobs/search/advanced` and `POST /jobs/search/advanced/resume` (`routers/search_advanced.py`, `search/advanced/`): a tool-calling LangGraph agent with an LLM (`langchain-openai` `ChatOpenAI`; Groq by default, or any OpenAI-compatible endpoint via the optional `ADVANCED_LLM_BASE_URL` / `ADVANCED_LLM_MODEL`; the agent always sends `GROQ_API_KEY`, so another base URL must accept that key, and pointing it at another provider needs a key change in code). It needs a saved CV (`ApplicantProfile`, else 400), which is the redacted text from the CV page (below); the CV and the per-user preference memory only enter the run context, never the checkpoint. Five tools, one call per turn: `update_brief`, `search_jobs` (1–3 query variants through the normal retrieval + rerank, the observation is match counts), `inspect_job`, `ask_user` (a question with options; the run interrupts) and `finish`. The brief separates **stated** requirements (locked: only the user relaxes one, by answering a question) from **inferred** ones (from the CV and memory; the agent may trade them; a preference such as "preferably remote" is always inferred, never locked), and probes ("what if …" searches) test a stated item's cost before asking. A brief item that maps to an index field carries a value from the index vocabulary (`search/advanced/vocabulary.py`, the values ingestion writes: `on-site`, `full-time`, `english`, …; the tool schema lists them and the brief normalises `onsite` / `en` / `remote|hybrid`); location type, seniority and employment type take a list meaning any of them, at most one stated item per field. Stated items are hard filters on every search, and a found job is shortlisted only if it positively meets every stated item whose field it carries (unknown counts as not met). `search_jobs` is refused before the first `update_brief`; `ask_user` before the first search. Then a `finalize` step scores the whole pool of found jobs (fit score, fits, gaps, trade-offs) and writes the summary, so the shortlist is not limited to the last search. Budget: 6 turns, 9 searches (2 of them probes), 2 questions, 4 minutes per request (agent LLM calls stop 45 s before the deadline, which is kept for finalize; output tokens capped per call); a spent budget still ends with a normal shortlist (`done`). `partial` means the assistant could not finish: an LLM error, an unparsed tool call, or a failed or timed-out finalize; it returns the most relevant eligible jobs found, without fit fields. Runs use `durability="exit"` and the thread is deleted when a run ends (results, or an exception: a `finally` in `run_agent`), so a thread lives only while a question is open; `/resume` of a thread with no open question is 404. The API answers with results (`status` `done` / `partial`: brief, summary, searches, jobs with fit and trade-offs) or a question (`status` `question`, `thread_id`, text + options, answered by `/resume` with the option label or free text); 404 for an unknown or finished thread, 503 when the LLM is not configured. Not built yet: one active run per user, the 24 h sweep of abandoned threads and the progress stream (the frontend waits on the JSON call).

**CV page and Agent mode** — The user uploads (PDF/DOCX/TXT, text extracted by `POST /users/cv/extract`, nothing stored) or writes the CV at `/cv`; "Remove private info" (`POST /users/cv/redact`, rule-based `backend/cv/redact.py`) replaces obvious personal data with placeholders (`[EMAIL]`, `[PHONE]`, `[NAME]`, …), and `PUT /users/cv {text, confirm}` answers 409 with the findings until the user confirms ("Save anyway"); saving empty text deletes the CV. LLM calls see only the saved text, so the agent sees the redacted CV; search queries and preference memory are not checked. Migration 015 deleted every CV stored before this check, plus the LangGraph checkpoints. Basic search uses neither the CV nor the preference memory; the UI calls advanced search **Agent** mode: the Basic/Agent switch sits under the search bar (Agent makes the bar glow), choosing Agent without a saved CV asks whether to add one (`has_cv` from `GET /auth/me`), and the preference memory is edited at `/preferences`. The sidebar holds only the Search / CV / Preferences links and Logout.

### Backend Processes & Outbox Pattern

Three entrypoints (split for k8s):
- `backend.main:app` — API only; lifespan loads ML models, ensures the OpenSearch index/pipeline, and opens a LangGraph `AsyncPostgresSaver` checkpointer. No background sync tasks.
- `python -m backend.worker` — runs `reconcile_worker` + `retention_worker` from `outbox/worker.py` and `backup_worker` from `backup.py` (singleton Deployment in k8s)
- `python -m backend.bootstrap` — one-time setup: checkpointer tables + dropping legacy OpenSearch indexes (the k8s bootstrap Job runs `alembic upgrade head` first)

Jobs and companies are written atomically to Postgres with an `outbox` row. Every 5 minutes `reconcile` bulk re-indexes to OpenSearch: jobs with unprocessed outbox rows plus live jobs (posted within `JOB_LIVE_DAYS`, default 14, by `posted_at`, else `created_at`; `backend/live.py`) missing from the index; `company_upserted` events patch company-derived fields on that company's job docs via `update_by_query()`. Every 6 hours `retention_worker` deletes OpenSearch docs no longer live (by the doc's `posted_at`). `GET /companies/pending` uses the same window, and `GET /jobs/stored` lists the jobs stored within it (URL, title, company), which ingestion skips. When `BACKUP_SBOX_*` is set, `backup_worker` runs a daily `pg_dump` + rclone upload to a Hetzner Storage Box (7-day rolling retention).

### Ingestion Pipeline

`ingestion/pipeline/__main__.py` runs end-to-end. Its timing follows from one number, the hours between scheduled runs (`--interval-hours`, default 2; Helm `ingestion.intervalHours`, tofu `ingestion_interval_hours`, compose `INGESTION_INTERVAL_HOURS`; it must divide 24): the schedule (`0 */N * * *`), the kill deadline 10 minutes before the next run (CronJob `activeDeadlineSeconds`, `timeout` on ECS and compose), the look-back of two intervals (`--hours` overrides it), so one missed run loses nothing, and company enrichment until 20 minutes before the next run:
1. Scrape job offers for a *scope* (`offer/scraping/scopes.py`: search terms plus a title rule; `ai` covers technical roles in ML, AI, LLM, GenAI, NLP, computer vision, data science and data engineering, and drops sales, marketing, recruiting, legal, product-management and data-labelling titles unless the title names an engineer/scientist/developer/architect/researcher; set by Helm `ingestion.scope`, tofu `offer_scope`, compose `OFFER_SCOPE`) from sources (jobspy, adzuna, arbeitnow, freehire; what each can do and why others were dropped is in `ingestion/SOURCES.md`) via `ingestion.offer.scraping`; a source that fails or raises is skipped and the others carry on. Each source gets the terms in the form it understands: one OR query on LinkedIn (up to 80 jobs kept a run, 1–3 s before each description request, and the first 429 stops LinkedIn for the rest of the run, dropping the job that got it so the next run retries it), Indeed and Glassdoor, `what_or` sorted by date on Adzuna (hours cut locally, it filters whole days), one search per term on freehire (no OR: a multi-word query requires its first word), the whole feed on Arbeitnow (3 pages, paced for Cloudflare). Every job then has to have a title in Latin script (accents included, so European titles pass; Cyrillic, Greek, CJK and the like do not: `filters.is_latin_script`) and pass the scope's title rule, since boards that match descriptions bring plenty of jobs that merely mention AI, and jobs already stored (`GET /jobs/stored`: the same URL, or the same title + company under another URL, i.e. a repost or another board's listing; `scraping/stored.py`) are skipped before any description fetch, parse or embedding, and the stored job is left as it is; LinkedIn checks all of this on the card, before the extra request for its description. Within a run, the same title + company from several sources is kept once (`deduplicator.py`, same key). Without the stored jobs (backend unreachable) nothing is skipped. freehire.me (`freehire_source.py`) is a keyless aggregator of employers' ATS boards, EURES and the Bundesagentur, with full descriptions; its window is on `created_at` (when freehire indexed the posting, often hours after `posted_at`), and postings it flags as stale re-posts or evergreen, or posted more than `JOB_LIVE_DAYS` ago, are skipped. `posted_at` keeps the time where a board gives one (Indeed's timestamp, LinkedIn's "N hours ago", freehire, Adzuna); Glassdoor and older LinkedIn cards give only the day, stored as midnight UTC, which the job card shows as "today" / "N days ago"
2. Fetch full description if missing (requests + trafilatura; Playwright is used only by company-page scraping)
3. Parse each offer **with rules** (`offer/parsing/`): `sections.py` turns raw HTML into lines and cuts job-board clutter (Adzuna's salary/similar-jobs tail, badges), splits the description on headings and drops company/benefits/apply/legal blocks and EEO boilerplate, and drops the company pitch that opens many descriptions without a heading (its sentences up to the first one about the role: hiring words, "as a …", the job title; the signal lists are in `lexicon.py`); a company block that clearly describes the role keeps it, and a short line that reads like a heading of a kept section stays (it is often a skill: "Strong SQL skills"). On 100 hand-checked jobs (2026-10) about two thirds come out as the role alone; `rules.py` derives seniority, employment/location type, office, salary, text language (lingua) and required languages from multilingual tables in `lexicon.py`. Then one LLM call summarizes the offer (`parsing/summary.py`: role info, requirements, responsibilities, domain as short phrases, from the cleaned description) through its own pool, `OFFER_LLM_PROVIDERS` (default NVIDIA → Gemini; Groq is left out, its daily tokens would not last a call per job); a provider paused by a per-minute 429 is waited out (up to 90 s, `wait_up_to`), since skipping would leave every job parsed meanwhile without one; when every provider fails the job simply has no summary chunk.
4. Embed: kept text → ≤4 chunks of ~400 tokens (64 overlap), each `title\nwindow`, plus `title\nsummary` first, flagged `summary: true`, when there is one; encoded with `passage: ` by `intfloat/multilingual-e5-small` (`embedder.embed_chunks`). On a blind comparison against main's LLM-summary ranking (2026-10, 8 queries over the same 732 jobs) the plain chunks scored lower (35 against 44); chunks plus the summary chunk, with the reranker reading the summary, scored higher (45 with the summaries this code writes, at 99% coverage; only the summary as the vector, 38)
5. POST jobs (with `chunks`), then upsert each company with the facts the job board shipped (LinkedIn: company page + industry; Indeed: website + description; never Indeed's unreliable head count). Offers without a company name are dropped at scrape time.
6. Enrich companies until 20 minutes before the next run (~20 s a company; a run stops early once nothing is pending) from `GET /companies/pending`: companies with live jobs that are never attempted, still empty after an attempt ≥7 days ago (≤3 attempts), or last enriched over a year ago (`refresh`, searched from scratch), ordered by their newest job, so the employers of the jobs just posted come first and the backlog fills the remaining time. Each costs two web searches (`company/scraping/searcher.py`): `"<name> <city> <country> company"` (the city disambiguates namesakes) and `"<name> glassdoor reviews"` restricted to Glassdoor. Searches go through a provider pool (`company/scraping/providers.py`, order from `SEARCH_PROVIDERS`, default TinyFish → Exa → Tavily; providers without a key are skipped): a 429 pauses that provider (Retry-After, else 60 s), an out-of-credits/bad-key status pauses it for an hour (`UNUSABLE_PAUSE`; it still counts as out meanwhile), a network error or 5xx moves the query to the next one. Only Tavily returns a short answer; otherwise the LLM reads the snippets and the scraped company pages. Then: job-board facts → website JSON-LD → Wikidata → Glassdoor regex → one small LLM call (~2k tokens) on the answer/snippets/pages, or rules on the answer when the LLM is unavailable → heuristics. The LLM call goes through a pool (`llmpool/llm.py`, OpenAI-compatible chat completions in JSON mode; order from `LLM_PROVIDERS`, default NVIDIA Nemotron 3 Ultra, then 3.5 Lightning, with thinking off → Gemini Flash-Lite → Groq), on the same base as the search pool (`llmpool/pool.py`): 429 pauses, a daily quota/bad key pauses it for an hour, an unknown model moves to the provider's next model (`NVIDIA_MODEL` etc. are comma-separated lists) and pauses it after the last, a timeout, 5xx or an answer that does not parse goes to the next provider; each provider is paced (Groq ≥12 s for its 8k tokens/minute). The website (known → searched → Wikidata's official website) is only taken from a search result whose *registered* domain contains the company name (aggregators are blocklisted; `utils/urls.py`), so careers subdomains become the homepage and hiring platforms like `x.myworkdayjobs.com` drop out; job-board websites go through the same check. A Wikidata match whose website disagrees with the known/searched one is a namesake and is ignored entirely. The post carries `enrichment_attempted`, which stamps `enriched_at` / `enrich_attempts` even when nothing was found; a company whose enrichment raises is stamped too, so a recurring error cannot bring it back first in every run. Once every search or every LLM provider is out for the run, the remaining companies wait for the next run (they would otherwise use up an attempt); company enrichment never affects the exit code. The run exits 1 when >20% of job posts fail.

After changing the rules or the embedder, `uv run python -m ingestion.backfill` re-derives every stored job (pages `GET /jobs/?after=&limit=`), summaries included unless `--no-summary` (a job whose summary fails is posted with the plain chunks). `POST /jobs/` overwrites the derived fields whenever the post carries a description (they are a pure function of it); without one it only fills empty fields. Companies use fill-empty, since their data comes from network lookups and the LLM, with one exception: an enrichment of a company last enriched over a year ago (`ENRICH_REFRESH_DAYS`) replaces the stored values with whatever it found, so wrong or outdated data eventually gets corrected. Job-board facts never replace anything.

### Data Models

- **`Company`** — unique by `name`; enriched with employee_count, founded_year, review_score, financial_health_score (int 1-5, only with evidence), is_consulting, is_startup, industry, country, linkedin_url, topic; `enriched_at` / `enrich_attempts` drive the enrichment retry and the yearly refresh
- **`Job`** — unique by `url`; stores parsed fields (employment_type, location_type, seniority, languages_required) and `chunks` JSONB (text + 384-d embedding each; the LLM summary chunk carries `summary: true`)
- **`Outbox`** — transient sync table; `processed_at` is NULL until worker drains it
- **`User`**, **`ApplicantProfile`** (`models.py`) — auth accounts and per-user CV text
- **`PreferenceMemory`** (`search/advanced/models.py`) — one row per user; `memory_text` is LLM-distilled from advanced-search sessions in a background task, and `user_edited` marks a hand-written summary the distiller may only append to

### OpenSearch Index (`jobs_v3`)

Chunk vectors live in a nested `chunks` field (`text` + 384-dim `embedding` + `summary` flag + the job's `posted_at`, for the knn filter); bootstrap drops the legacy `jobs` / `jobs_v2` indexes and reconcile repopulates `jobs_v3` from Postgres (`jobs.chunks` JSONB). A mapping change that needs every doc rewritten bumps the index name the same way. Embedding dimension: 384. HNSW with cosine similarity via Lucene engine. Hybrid pipeline name: `hybrid-pipeline` (configured at startup via `PUT /_search/pipeline`).

### DB Migrations

Alembic is in `backend/alembic/`. Add schema changes there; the compose backend container runs `alembic upgrade head` at startup, and in k8s the bootstrap hook Job runs it before rollout.

## Key Dependencies

| Component | Library |
|-----------|---------|
| API framework | FastAPI + uvicorn |
| ORM / async DB | SQLAlchemy async + asyncpg |
| Vector search | opensearch-py[async] |
| Bi-encoder (embed) | sentence-transformers (`intfloat/multilingual-e5-small`) |
| Cross-encoder (rerank) | sentence-transformers (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) |
| Language detection | lingua-language-detector |
| LLM | Ingestion: `llmpool` OpenAI-compatible pool over NVIDIA NIM / Gemini / Groq (`requests`); backend: no Groq SDK, the advanced agent uses `langchain-openai` `ChatOpenAI` against Groq's OpenAI-compatible API (or `ADVANCED_LLM_*`) |
| Agents / checkpointing | LangGraph + `langgraph-checkpoint-postgres` (advanced search) |
| Auth | python-jose (JWT) + passlib[bcrypt] |
| Scraping | python-jobspy, Playwright, trafilatura |
| Company web search | TinyFish / Exa / Tavily REST APIs (provider pool) |
| Package manager | uv workspace |
