# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

**Jobsifty** is a job search ranking system. It scrapes job offers from multiple sources, enriches them with rule-based metadata and chunked multilingual embeddings (no LLM on the offer path), stores them in Postgres + OpenSearch, and serves a hybrid BM25 + k-NN search endpoint with cross-encoder reranking.

## Repository Structure

This is a `uv` workspace with two packages:

- **`backend/`** — FastAPI service: REST API (JWT auth), Postgres (SQLAlchemy async), OpenSearch hybrid search, outbox reconcile worker, LangGraph agent (advanced search)
- **`ingestion/`** — Pipeline: job scraping, deterministic offer parsing, company enrichment (LLM only for judgement fields), chunked embedding, and HTTP posting to backend

Other top-level directories:
- `frontend/` — React (Vite) SPA, served by nginx in Docker/k8s
- `deploy/` — Helm chart (`deploy/helm/jobsifty/`) + k8s runbook (`deploy/k8s/README.md`); **AWS showcase** is the ECS managed path in `deploy/infra/aws/` (OpenTofu + Fargate/RDS/OpenSearch Service — not Helm on AWS)
- `dumps/` — `jobsifty.current.dump` is the demo database every target restores from / captures into (gitignored; seed with `deploy/scripts/seed-dump`)
- `tailor/` — standalone CV-tailoring CLI scripts (gitignored, local docx/cover-letter scripts; **not** imported by `backend/` or `ingestion/`, do not wire new code to it)

The autofill feature (Chrome extension + tailorer LangGraph agent that drafted CVs/cover letters and filled application forms) lives on the `autofill` branch, not on `main`.

[`README.md`](./README.md) is the human-facing counterpart: same architecture, with diagrams and the reasoning behind each choice. Keep the two consistent when behaviour changes.

Ad-hoc CLIs (run from `ingestion/`):
- `uv run python -m ingestion.offer "<query>"` — offer scraping
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
it includes Playwright and python-jobspy/tls-client. Nodes are CX33 (x86);
do not pull arm64-only images onto them.

### AWS ECS specifics

Full runbook in `deploy/infra/aws/README.md`. Things that differ from the Helm path:

- **Migrations run as a one-shot RunTask, not a hook Job.** `deploy/scripts/run aws up` fires it (`aws_run_bootstrap_task`) between apply and dump restore; a bare `tofu apply` does not, so after a manual apply you must `aws ecs run-task` the `bootstrap` task definition yourself. A green ALB `/health` does **not** mean the DB is migrated.
- **Ingestion is an EventBridge Scheduler `RunTask`** (every 2h), and it posts to the internal Cloud Map address `http://api.<project>.local:8000` — never the public hostname, so a run cannot write into whichever stack currently owns DNS.
- **Secrets** live in one Secrets Manager entry; each key is injected per container via `secrets[].valueFrom` (`local.app_secret_keys` in `ecs.tf`). Adding an env var means adding it there.
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
uv run python -m ingestion.pipeline "machine learning engineer" --hours 48
uv run pytest                              # run all ingestion tests
```

### Required Environment Variables

**Secrets** — create a local `.env` from `.env.example` (gitignored):

```
GROQ_API_KEY=...
SECRET_KEY=...                          # JWT signing
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
GROQ_MODEL_BASE=...                     # company enrichment (offers no longer use an LLM)
OFFER_QUERY=...                         # ingestion Docker / CronJob query
COMPANY_ENRICH_PER_RUN=6                # companies enriched per ingestion run (2 searches + 1 Groq call each)
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
   query text (`search/query_parsing.py`). The LLM-based
   `search/query_understanding.py` is used only by the separate advanced-search
   endpoint (`POST /jobs/search/advanced`, `search/advanced/`), which is
   work-in-progress.
2. **Hybrid retrieval** — OpenSearch hybrid query: BM25 on `description` + nested k-NN on `chunks.embedding` (384-dim, `intfloat/multilingual-e5-small`, query encoded with a `query: ` prefix via `encode_query()`; a job scores by its best chunk via `score_mode: max`; hybrid queries drop `inner_hits`, so hits return the chunk vectors in `_source`), results combined via min-max normalization with 50/50 weights (`search/retrieval.py`). Filter clauses are soft by default (scored `should` with boost=2) so near-misses still surface; words like "strictly" / "exactly" / "no exceptions" in the query set `SearchFilters.strict`, applying them as a hard `post_filter` with a larger prefetch (200).
3. **Cross-encoder reranking** — `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (override with `RERANKER_MODEL`, `max_length=256`) reranks retrieved hits on the chunk closest to the query embedding, picked in Python from `_source.chunks` (title if a job has no chunks), returns top 20; torch threads follow `TORCH_NUM_THREADS` (default 2, matching the api pod's 2-CPU limit) (`search/reranker.py`)
4. **Postgres round-trip** — full `Job` + `Company` records fetched by ID for the response

**Auth**: `backend/auth/` + `routers/auth.py` — JWT bearer tokens (`POST /auth/register`, `POST /auth/login`, `GET /auth/me`); most endpoints depend on `get_current_user`.

**Advanced search (WIP)** — `POST /jobs/search/advanced` (`routers/search_advanced.py`, `search/advanced/`): a Postgres-checkpointed LangGraph agent that uses the Groq LLM and per-user preference memory. It interrupts with up to 2 clarifying questions (answered via `POST /jobs/search/advanced/resume`), can critique/refine its retrieval, and returns fit-scored results (the top 10 jobs, each sent to the LLM as title + company + the first 1,500 chars of its cleaned text). Requires an uploaded CV (`ApplicantProfile`).

### Backend Processes & Outbox Pattern

Three entrypoints (split for k8s):
- `backend.main:app` — API only; lifespan loads ML models, ensures the OpenSearch index/pipeline, and opens a LangGraph `AsyncPostgresSaver` checkpointer. No background sync tasks.
- `python -m backend.worker` — runs `reconcile_worker` + `retention_worker` from `outbox/worker.py` and `backup_worker` from `backup.py` (singleton Deployment in k8s)
- `python -m backend.bootstrap` — one-time setup: checkpointer tables + OpenSearch `created_at` backfill (the k8s bootstrap Job runs `alembic upgrade head` first)

Jobs and companies are written atomically to Postgres with an `outbox` row. Every 5 minutes `reconcile` bulk re-indexes to OpenSearch: jobs with unprocessed outbox rows plus live jobs (≤30 days old) missing from the index; `company_upserted` events patch company-derived fields on that company's job docs via `update_by_query()`. Every 6 hours `retention_worker` deletes OpenSearch docs older than 30 days. When `BACKUP_SBOX_*` is set, `backup_worker` runs a daily `pg_dump` + rclone upload to a Hetzner Storage Box (7-day rolling retention).

### Ingestion Pipeline

`ingestion/pipeline/__main__.py` runs end-to-end (`--hours` defaults to 72):
1. Scrape job offers from sources (jobspy, adzuna, arbeitnow, remotive) via `ingestion.offer.scraping`
2. Fetch full description if missing (requests + trafilatura; Playwright is used only by company-page scraping)
3. Parse each offer **without an LLM** (`offer/parsing/`): `sections.py` splits the description on headings and drops company/benefits/apply/legal blocks and EEO boilerplate; `rules.py` derives seniority, employment/location type, office, salary, text language (lingua) and required languages from multilingual tables in `lexicon.py`. There is no job summary any more: the kept text is only chunked and embedded.
4. Embed: kept text → ≤4 chunks of ~400 tokens (64 overlap), each `title\nwindow`, encoded with `passage: ` by `intfloat/multilingual-e5-small` (`embedder.embed_chunks`)
5. POST jobs (with `chunks`), then upsert each company with the facts the job board shipped (LinkedIn: company page + industry; Indeed: website + description; never Indeed's unreliable head count). Offers without a company name are dropped at scrape time.
6. Enrich at most `COMPANY_ENRICH_PER_RUN` companies (default 6, sized to Groq's free 200k tokens/day) from `GET /companies/pending`: companies with live jobs, never attempted or still empty after an attempt ≥7 days ago (≤3 attempts), most live jobs first. Each costs two web searches (`company/scraping/searcher.py`): `"<name> <city> <country> company"` (the city disambiguates namesakes) and `"<name> glassdoor reviews"` restricted to Glassdoor. Searches go through a provider pool (`company/scraping/providers.py`, order from `SEARCH_PROVIDERS`, default TinyFish → Exa → Tavily; providers without a key are skipped): a 429 pauses that provider (Retry-After, else 60 s), an out-of-credits/bad-key status disables it for the run, a network error or 5xx moves the query to the next one. Only Tavily returns a short answer; otherwise the LLM reads the snippets and the scraped company pages. Then: job-board facts → website JSON-LD → Wikidata → Glassdoor regex → one small Groq call (~2k tokens, spaced ≥12 s apart for the 8k tokens/minute free tier) on the answer/snippets/pages, or rules on the answer when the LLM is unavailable → heuristics. The website is only taken from a result whose domain contains the company name (aggregators are blocklisted). The post carries `enrichment_attempted`, which stamps `enriched_at` / `enrich_attempts` even when nothing was found. Once every search provider is out, or Groq hits its daily quota or an auth error, the remaining companies wait for the next run (they would otherwise use up an attempt); company enrichment never affects the exit code. The run exits 1 when >20% of job posts fail.

After changing the rules or the embedder, `uv run python -m ingestion.backfill` re-derives every stored job (pages `GET /jobs/?after=&limit=`). `POST /jobs/` overwrites the derived fields whenever the post carries a description (they are a pure function of it); without one it only fills empty fields. Companies always use fill-empty, since their data comes from network lookups and the LLM: a stored value is never replaced.

### Data Models

- **`Company`** — unique by `name`; enriched with employee_count, founded_year, review_score, financial_health_score (int 1-5, only with evidence), is_consulting, is_startup, industry, country, linkedin_url, topic; `enriched_at` / `enrich_attempts` drive the enrichment retry
- **`Job`** — unique by `url`; stores parsed fields (employment_type, location_type, seniority, languages_required) and `chunks` JSONB (text + 384-d embedding each)
- **`Outbox`** — transient sync table; `processed_at` is NULL until worker drains it
- **`User`**, **`ApplicantProfile`** (`models.py`) — auth accounts and per-user CV text
- **`PreferenceMemory`** (`search/advanced/models.py`) — one row per user; `memory_text` is LLM-distilled from advanced-search sessions in a background task, and `user_edited` marks a hand-written summary the distiller may only append to

### OpenSearch Index (`jobs_v2`)

Chunk vectors live in a nested `chunks` field (`text` + 384-dim `embedding`); bootstrap drops the legacy `jobs` index and reconcile repopulates `jobs_v2` from Postgres (`jobs.chunks` JSONB). Embedding dimension: 384. HNSW with cosine similarity via Lucene engine. Hybrid pipeline name: `hybrid-pipeline` (configured at startup via `PUT /_search/pipeline`).

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
| LLM | Groq SDK; agents use `langchain-openai` `ChatOpenAI` pointed at Groq's OpenAI-compatible API |
| Agents / checkpointing | LangGraph + `langgraph-checkpoint-postgres` (advanced search) |
| Auth | python-jose (JWT) + passlib[bcrypt] |
| Scraping | python-jobspy, Playwright, trafilatura |
| Company web search | TinyFish / Exa / Tavily REST APIs (provider pool) |
| Package manager | uv workspace |
