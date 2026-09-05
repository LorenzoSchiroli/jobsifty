# Jobsifty

**A job search engine that ranks openings against *you*, not against a keyword.**

Jobsifty continuously scrapes job offers from several sources, has an LLM
distil each posting into structured metadata, embeds it, and serves a hybrid
**BM25 + k-NN** search endpoint with **cross-encoder reranking**. On top of that
sit two LangGraph agents: an **advanced search** that asks clarifying questions
and fit-scores every result against your CV, and a **tailorer** that drafts a
tailored CV and cover letter and fills application forms from a Chrome side
panel.

It is a full vertical slice, deliberately: scraping and LLM enrichment →
retrieval and ranking → REST API and SPA → and the same stack deployed two
different ways, on **self-hosted Kubernetes (Hetzner)** and on **managed AWS ECS
Fargate**, from one command.

| | |
|---|---|
| **Retrieval** | OpenSearch hybrid search — BM25 + HNSW k-NN, score-normalised in a single round trip, then reranked by a cross-encoder |
| **Models** | `bge-small-en-v1.5` (384-d embeddings) + `ms-marco-MiniLM-L-6-v2` (rerank), both CPU-only; Groq-hosted LLMs for offline enrichment and agents |
| **Durability** | Postgres is the source of truth; the search index is derived and rebuildable, kept in sync by a transactional **outbox** |
| **Agents** | LangGraph graphs checkpointed in Postgres — they interrupt, ask, and resume across HTTP requests |
| **Infra** | OpenTofu + Helm (k3s on Hetzner) *and* OpenTofu + ECS Fargate (AWS), same images, same env contract, `deploy/scripts/run <target> up` |

---

## System overview

```mermaid
flowchart TB
    SRC["Job sources<br/>jobspy · adzuna · arbeitnow · remotive"]
    ING["ingestion · every 2h<br/>scrape → LLM parse → embed"]
    CLI["Clients<br/>React SPA · Chrome extension"]
    API["backend API<br/>FastAPI · JWT · search · agents"]
    PG[("Postgres<br/>source of truth + outbox")]
    WK["worker<br/>reconcile every 5 min"]
    OS[("OpenSearch<br/>derived index")]

    SRC --> ING
    ING -->|"POST /jobs, /companies"| API
    CLI --> API
    API <--> PG
    PG -->|"unprocessed outbox rows"| WK
    WK -->|"bulk re-index"| OS
    API <-->|"retrieve"| OS
```

Three processes, split so they scale and fail independently:

| Process | Role |
|---------|------|
| `backend.main:app` | API only. Loads the two ML models, ensures the OpenSearch index + search pipeline, opens the LangGraph checkpointer. Stateless → horizontally scalable. |
| `python -m backend.worker` | Singleton. Outbox reconcile (5 min), retention (6 h), nightly `pg_dump` backup. |
| `python -m backend.bootstrap` | One-shot. Checkpointer tables + `created_at` backfill; migrations run alongside it. |

**Ingestion** runs out-of-process as a scheduled batch (k8s CronJob / EventBridge
RunTask): scrape → fill missing descriptions with trafilatura → LLM-parse each
offer into an `OfferSummary` → embed `title + summary` → `POST` to the backend,
then upsert companies and enrich the ones whose profile is more than half empty.

### Why it is built this way

| Decision | Motivation |
|----------|------------|
| **Postgres is the source of truth, OpenSearch is derived** | A search index is a cache with opinions. Making it authoritative means a lost cluster is lost data, and every schema change becomes a migration you cannot roll back. Here the index can be dropped and rebuilt from Postgres at any time. |
| **Transactional outbox instead of dual writes** | Writing to Postgres *and* OpenSearch in one request means either a distributed transaction or silent divergence when the second write fails. The job and its `outbox` row are committed atomically; the worker drains the outbox and additionally re-indexes any live job (≤30 days) missing from the index, so the system self-heals instead of drifting. |
| **Ingestion talks to the public REST API, not to the database** | The pipeline holds no DB credentials and no ORM models. It is just another client, which is what lets it run as a CronJob on Kubernetes or a Fargate task on AWS with zero extra wiring — and what keeps validation in exactly one place. |
| **LLMs only offline and on the opt-in agent path** | Parsing a posting is a batch job where a 2-second LLM call is free; a search request is not. The hot search path is regex + two small local models, so it is fast, cheap, deterministic and unit-testable. |
| **CPU-only models** | `bge-small` (384-d) and `MiniLM-L-6` were chosen to fit a demo budget with no GPU anywhere in the stack — the whole thing runs on a single small Hetzner x86 VPS and on `db.t4g.micro` / `t3.small.search`-class AWS resources. |
| **uv workspace, two Python packages** | `backend` and `ingestion` share a lockfile and Python version but ship as separate images with separate dependency sets — the API image does not carry Playwright, the scraper does not carry SQLAlchemy models. |

---

## The recommendation engine

Two ranking modes share the same retrieval core: a fast deterministic one for
every query, and an agentic one for when you want the system to think.

### Base search — no LLM in the hot path

`POST /jobs/search`, body `{"query": "..."}`, JWT-protected.

```mermaid
flowchart TB
    Q["query text"]
    P["1 · parse_query<br/>regex → SearchFilters + semantic_query"]
    E["2 · embed<br/>bge-small-en-v1.5 → 384-d"]
    H["3 · hybrid retrieve<br/>OpenSearch: BM25 + k-NN, fused"]
    R["4 · rerank<br/>cross-encoder → top 20"]
    D["5 · hydrate<br/>Postgres, by job_id"]

    Q --> P --> E --> H --> R --> D
```

Step 3 is where the interesting decisions live, so zoomed in:

```mermaid
flowchart TB
    F["SearchFilters<br/>seniority · location_type · languages · …"]

    subgraph LEGS["hybrid query"]
        BM["BM25 leg<br/>on description"]
        KNN["k-NN leg<br/>HNSW cosine, k=100"]
    end

    N["min-max normalise<br/>arithmetic mean 50 / 50"]
    OUT["ranked hits → rerank"]

    F -->|"default: scored should, boost 2.0"| LEGS
    BM --> N
    KNN --> N
    N --> OUT
    F -.->|"strict: hard post_filter,<br/>prefetch 200"| OUT
```

| Choice | Motivation |
|--------|------------|
| **Hybrid BM25 + k-NN, not one or the other** | Lexical search nails the tokens that must match literally — `Kubernetes`, `SAP`, `PyTorch`, a specific certification. Dense retrieval catches the paraphrase — "ML engineer" ≈ "applied scientist". Job postings need both: they are keyword-dense *and* written in a hundred different dialects. |
| **Fusion inside an OpenSearch search pipeline** | BM25 scores and cosine similarities live on incomparable scales, so they are min-max normalised before an arithmetic mean with 50/50 weights. Doing it server-side in the `hybrid` query means one round trip and one ranked list, instead of two queries merged in Python. |
| **Filters are *soft* by default** | The filterable fields (`seniority`, `location_type`, `languages_required`, …) come from an LLM parse of free-text postings, so they are good but not perfect. A hard filter turns one bad extraction into a silently missing result. Instead the clauses are scored `should` clauses with `boost: 2.0` — matching documents win, near-misses still surface. Writing *"strictly"*, *"exactly"* or *"no exceptions"* flips `SearchFilters.strict`, which applies them as a real `post_filter` and raises the prefetch to 200 so the filter has candidates left to keep. |
| **Cross-encoder rerank on the shortlist only** | The bi-encoder scores query and document independently — necessary, because embeddings must be computed at index time. A cross-encoder reads the pair jointly and is markedly more accurate, but costs one forward pass *per candidate*. Applying it to the top 20–200 retrieved hits buys most of the accuracy for a bounded, predictable cost. |
| **Rerank on `summary_text`, not the raw description** | Postings are padded with boilerplate ("we are an equal opportunity employer", benefits, legal notes). The LLM-generated summary is the signal; feeding it to a 512-token cross-encoder avoids spending the whole window on scaffolding. |
| **384 dimensions, HNSW + cosine on the Lucene engine** | Small vectors keep index size and HNSW memory low, `bge-small` is near the top of its size class on MTEB, and the Lucene engine needs no native plugin and supports filtered k-NN. |
| **A `parse=… embed=… retrieve=… rerank=… db=…` timing line per query** | Each stage is logged separately, so a latency regression points at a stage instead of at "search is slow". |

### Advanced search — a checkpointed LangGraph agent (WIP)

`POST /jobs/search/advanced` → `POST /jobs/search/advanced/resume`. Requires an
uploaded CV.

```mermaid
flowchart TB
    A["POST /jobs/search/advanced<br/>query + CV + preference memory"]
    B["generate_questions · at most 2"]
    C["clarify — interrupt()<br/>state saved by thread_id, request returns"]
    E["search<br/>retrieve + rerank"]
    F{"critique"}
    G["fit_score<br/>0-100 + rationale + gaps"]
    M[("preference memory")]

    A --> B --> C
    C -->|"POST /resume<br/>thread_id + answers"| E
    E --> F
    F -->|"refine · once at most"| E
    F -->|"good enough"| G
    G -->|"background: distil"| M
    M -.->|"next search"| A
```

| Choice | Motivation |
|--------|------------|
| **A state graph with a Postgres checkpointer, not an in-memory conversation** | The agent *interrupts* to ask its clarifying questions, so the run has to survive an HTTP round trip. Checkpointing to Postgres by `thread_id` puts that state in the database rather than in one process's memory — so the `/resume` call can land on any API replica, which is what makes the agent compatible with the HPA and with more than one Fargate task. |
| **At most two clarifying questions** | Users abandon interrogations. The prompt is explicitly allowed to return fewer, or none, when the query is already unambiguous. |
| **The self-critique loop runs at most once** (`refined_once`) | Critique-and-retry loops are the classic way to turn a 3-second agent into a 40-second one with an unbounded LLM bill. One refinement is where most of the gain is; the flag makes the worst case provable rather than hoped-for. |
| **Fit scoring returns `fit_score`, `fit_rationale` *and* `fit_gaps`** | A bare 0-100 number is unactionable. The gaps field turns the ranker into advice: what you would have to learn or show to become a plausible candidate. |
| **Preference memory is distilled in a background task** | The user gets results at once; the LLM that merges this session's signals into their long-term preference summary runs after the response is sent. A `user_edited` flag protects a hand-written summary — the distiller may only *append* to it, never rewrite it. |
| **Every LLM response is parsed defensively** | Each call has a typed fallback (empty question list, `need_refine: false`, zero-score results), so a malformed model output degrades the ranking instead of returning a 500. |

### Tailorer

A second LangGraph agent (`backend/tailorer/`), driven over a WebSocket from the
Chrome side panel: it maps the fields of the application form in front of you,
drafts a tailored CV and cover letter from your `ApplicantProfile`, and streams
fill instructions back to the page. The graph *interrupts* to hand those
commands to the browser and waits for what the page actually managed to fill
before continuing — the extension, not the model, is the thing that touches the
DOM.

---

## Data plane

```mermaid
flowchart TB
    W["POST /jobs, /companies"]
    TX["one transaction:<br/>row + outbox row"]
    PG[("Postgres")]
    RC["reconcile · every 5 min<br/>unprocessed outbox rows +<br/>live jobs missing from the index"]
    OS[("OpenSearch")]

    W --> TX --> PG
    PG --> RC
    RC -->|"bulk index"| OS
    RC -->|"set processed_at"| PG
```

Jobs and companies are written to Postgres together with an `outbox` row in the
same transaction. Every 5 minutes the reconcile worker bulk re-indexes: jobs
with unprocessed outbox rows, plus any live job (≤30 days old) missing from the
index. `company_upserted` events patch company-derived fields across that
company's job documents with a single `update_by_query`. Every 6 hours retention
drops OpenSearch documents older than 30 days, keeping the index at "what you
could actually still apply to". With `BACKUP_SBOX_*` set, the worker also runs a
daily `pg_dump` + rclone upload to a Hetzner Storage Box on a 7-day rolling
retention.

The `jobs` index carries the embedding, the two text fields used for scoring
(`description` for BM25, `summary_text` for reranking), and the keyword/numeric
fields the filter clauses target — `employment_type`, `location_type`,
`seniority`, `languages_required`, `is_consulting`, `is_startup`, `industry`,
`country`, `review_score`, `financial_health_score`, `created_at`.

---

## Infrastructure

The same application ships to three targets behind one script. Each target
configures itself; targets are independent, and whichever one is up owns the
domain.

```bash
deploy/scripts/run local                   # kind + Helm on this machine
deploy/scripts/run hetzner up              # tofu apply → helm → restore dump, DNS follows
deploy/scripts/run aws up --no-dns         # tofu apply → ECS, domain left where it is
deploy/scripts/run aws down --yes          # dump → promote → tofu destroy, no prompt
```

Every target, `local` included, restores from and captures into
`dumps/jobsifty.current.dump` (seed it with `deploy/scripts/seed-dump`), so the
demo database follows the stack between providers and a kind cluster shows the
same data as production. Bringing a cloud target up points Cloudflare
`app` / `api` / apex / `www` at it; taking it down removes those records.
`--no-dns` brings a stack up without claiming the domain — reach it by its load
balancer hostname instead. Bring the previous target down first, or the second
apply fails on a Cloudflare record conflict rather than silently stealing the
domain.

### Hetzner — self-hosted Kubernetes

OpenTofu drives the [`kube-hetzner`](https://github.com/kube-hetzner/terraform-hcloud-kube-hetzner)
module (k3s), then Helm installs the whole stack from `deploy/helm/jobsifty/`.

```mermaid
flowchart TB
    U(["users"]) --> CF["Cloudflare DNS"]
    CF --> TR["Traefik ingress<br/>+ cert-manager"]

    subgraph K3S["k3s · kube-hetzner + OpenTofu"]
        subgraph PERM["permanent pool · 1× CX33"]
            FE["frontend"]
            AP["api<br/>HPA 1→4 @ 70% CPU"]
            WK["worker"]
            PGS[("postgres")]
            OSS[("opensearch")]
        end
        subgraph BURST["burst pool · autoscaler 0→2× CX33"]
            CJ["ingestion<br/>CronJob, every 2h"]
        end
    end

    TR --> FE
    TR --> AP
    AP --> PGS
    AP --> OSS
    WK --> PGS
    CJ -->|"http://api:8000"| AP
    FE ~~~ AP
    PGS ~~~ OSS
```

*Not drawn:* the bootstrap Helm hook Job (migrations, index, checkpointer), the
`hcloud-volumes` CSI claims behind the two StatefulSets, the worker's own writes
to OpenSearch, and its nightly `pg_dump` + rclone push to a Hetzner Storage Box.

| Choice | Motivation |
|--------|------------|
| **Kubernetes on a cheap VPS rather than a managed control plane** | The chart is the portable artefact: the same `helm install` runs on a local kind cluster and on Hetzner, so "works on my machine" and "works in production" are the same code path. Hetzner keeps the always-on cost to a single small VPS plus volumes. |
| **Two node pools, one of them scale-to-zero** | Ingestion is the heavy, bursty, and by far the most fragile workload (Playwright, `python-jobspy`, TLS-fingerprinting clients). Pinning it to an autoscaler pool that idles at **0 nodes** means a scrape run cannot starve the API of CPU, and a crash loop costs nothing while idle. Stateful services and the API are pinned to the permanent pool so volumes never chase a node. |
| **HPA on the API at 70% CPU, 1→4** | The API is the only stateless component; everything else is deliberately a singleton or a StatefulSet. `deploy/k8s/loadtest-job.yaml` is an in-cluster k6 job that drives the autoscaler on demand. |
| **cert-manager + Traefik, apex and `www` redirected to `app`** | TLS renews itself, and there is exactly one canonical origin instead of four hosts with subtly different CORS and cookie behaviour. |
| **Nightly backup inside the worker** | No separate image, CronJob or operator for a single `pg_dump`: it is a loop in the process that is already a singleton, gated on the backup env vars being present. |
| **CX33 (x86) nodes, images built for `linux/amd64`** | ARM CAX capacity was unavailable in-region, and an arm64-only image on an x86 node fails at pull time. The GitHub Actions **Build and push images** workflow publishes amd64 images to GHCR for exactly this reason. |

### AWS — managed ECS Fargate

A second, independent implementation of the same system in `deploy/infra/aws/`
— OpenTofu only, no Kubernetes, deliberately built on managed services.

```mermaid
flowchart TB
    U(["users"]) --> CF["Cloudflare DNS"]
    CF --> ALB["ALB · ACM<br/>host rules: app / api"]

    subgraph VPC["VPC · 2 AZs"]
        subgraph PUB["public subnets"]
            ALB
            NAT["NAT gateway<br/>egress for private tasks"]
        end
        subgraph PRIV["private subnets · no public IPs"]
            FE["frontend"]
            AP["api<br/>autoscale 1→4"]
            ING["ingestion<br/>EventBridge, 2h"]
            WK["worker"]
            RDS[("RDS<br/>Postgres 16")]
            OSD[("OpenSearch<br/>Service")]
        end
    end

    ALB --> FE
    ALB --> AP
    ING -->|"Cloud Map"| AP
    AP --> RDS
    AP --> OSD
    WK --> RDS
    FE ~~~ AP
    RDS ~~~ OSD
```

*Not drawn:* the one-shot bootstrap RunTask, the single Secrets Manager entry
injected key-by-key into every task, CloudWatch log groups (7-day retention), the
S3 dump bucket the demo database rides in, the AWS Budgets alarm, and the
worker's own writes to OpenSearch.

| Choice | Motivation |
|--------|------------|
| **ECS Fargate, not EKS** | Running Kubernetes on both providers would have proven nothing twice. The interesting exercise is porting the same containers and the same environment contract onto a genuinely different substrate — managed services, IAM, task definitions — and seeing which assumptions were really Kubernetes assumptions. It is also far cheaper: no control-plane fee, no node fleet. |
| **Every task in private subnets, egress via one NAT gateway** | Nothing but the ALB is reachable from the internet, and the RDS and OpenSearch security groups admit *only* the ECS task security group. One NAT instead of one per AZ is an explicit availability-for-cost trade, appropriate for a demo. |
| **OpenSearch with fine-grained access control inside the VPC** | The backend authenticates as an internal-database user over HTTP basic auth, which is not an IAM principal — so an IAM-scoped domain policy would reject it with a bare 403 before FGAC ever sees the credentials. The domain policy is therefore permissive by design, and the domain stays closed because it has no public endpoint and its security group admits only the ECS tasks. |
| **Ingestion posts to a Cloud Map address, never the public hostname** | Public DNS may be pointing at the *other* provider. `http://api.jobsifty.local:8000` resolves inside this VPC, so a scrape run can only ever write into its own stack — the mistake it prevents is silently ingesting into Hetzner while testing AWS. |
| **EventBridge Scheduler → `RunTask` instead of a long-running scheduler** | Ingestion is a batch job; paying for an idle container 23 hours a day to run it twice would be the Kubernetes CronJob's job, and Fargate's equivalent is a scheduled task with a retry policy. |
| **Target-tracking autoscaling that mirrors the Helm HPA** | Same signal, same 70% threshold, same 1→4 range, so a load test tells you something comparable on either provider rather than comparing two different tuning exercises. |
| **The database rides in an S3 dump across `up` / `down`** | A cloud demo that cannot be destroyed is a subscription. `run aws down` captures the database, promotes the dump, then destroys everything; `run aws up` restores it. State survives, the bill does not. |
| **A least-privilege deployer IAM policy, checked in as `.example`** | `deployer-policy.example.json` documents exactly what the stack needs; the filled-in copy carrying a real account ID is gitignored, the same split as `terraform.tfvars`. |
| **AWS Budgets with 80% / 100% alerts, 7-day log and dump retention** | Cost is a design constraint here, not an afterthought — as are `skip_final_snapshot`, single-AZ RDS and a one-node OpenSearch domain. All of them are demo-scale choices, and all of them are the first things to change for real traffic. |

---

## Quick start

### 1. Configuration

Secrets live in a gitignored `.env`; non-secrets are committed in `.env.public`.

```bash
cp .env.example .env      # then fill in the keys below
```

| Variable | Where | Purpose |
|----------|-------|---------|
| `GROQ_API_KEY` | `.env` | LLM parsing, agents |
| `SECRET_KEY` | `.env` | JWT signing |
| `SERPERDEV_API_KEY` | `.env` | Company enrichment web search |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | `.env` | Optional job source |
| `DDGS_PROXY` | `.env` | Optional proxy for DuckDuckGo scraping |
| `BACKUP_SBOX_HOST` / `_USER` / `_RCLONE_PASS` | `.env` | Optional nightly Postgres backup |
| `GROQ_MODEL_LARGE` / `GROQ_MODEL_BASE` | `.env.public` | Model IDs for agents / parsing |
| `OFFER_QUERY` | `.env.public` | Query used by the ingestion container / CronJob |
| `CORS_ORIGINS` | `.env.public` | Extra browser origins for the API |
| `VITE_API_URL` | `.env.public` | API base URL baked into the frontend image |

> **Keep values unquoted.** `kubectl create secret --from-env-file` stores quotes
> verbatim (unlike dotenv), so a quoted `GROQ_API_KEY` yields a Groq
> `401 invalid_api_key` in Kubernetes.

### 2. Run the stack

```bash
deploy/scripts/run local                   # kind + Helm (canonical path)

docker compose up -d postgres opensearch   # or: dependencies only
docker compose up --build                  # or: full stack via compose
```

The frontend is on `:3000`, the API on `:8000` (`/docs` for OpenAPI, `/health`
for the load-balancer probe).

### 3. Run the backend from source

```bash
cd backend
uv run alembic upgrade head                # apply DB migrations
uv run uvicorn backend.main:app --reload   # dev server on :8000
```

## Commands

### Backend

```bash
cd backend
uv run pytest                              # all tests
uv run pytest tests/test_jobs.py           # one file
uv run pytest tests/search/test_filters.py::test_build_clauses_empty_when_all_none
```

Tests need a live Postgres at
`postgresql+asyncpg://postgres:postgres@localhost:5432/jobsifty_test`
(override with `TEST_DATABASE_URL`). OpenSearch and the ML models are mocked.

### Ingestion

```bash
cd ingestion
uv run python -m ingestion.pipeline "machine learning engineer" --hours 48
uv run pytest
```

Ad-hoc CLIs for debugging a single stage:

```bash
uv run python -m ingestion.offer "machine learning engineer" --hours 48 --sources jobspy,adzuna
uv run python -m ingestion.company "Stripe" "San Francisco" --debug
```

Offer sources: `jobspy`, `adzuna`, `arbeitnow`, `remotive` (default: all).

## Repository layout

| Path | What it is |
|------|------------|
| `backend/` | FastAPI service — REST API, Postgres (SQLAlchemy async), OpenSearch hybrid search, outbox worker, LangGraph agents |
| `ingestion/` | Pipeline — job scraping, LLM offer parsing, company enrichment, embedding, POST to the backend |
| `frontend/` | React (Vite) SPA, served by nginx in Docker/k8s |
| `extension/` | Chrome extension (Tailorer side panel; talks to the backend directly) |
| `deploy/helm/` | Helm chart for the whole stack + `values-local` / `values-cloud` / `values-hetzner` |
| `deploy/infra/hetzner/` | OpenTofu — kube-hetzner k3s cluster + Cloudflare records |
| `deploy/infra/aws/` | OpenTofu — VPC, ALB, ECS Fargate, RDS, OpenSearch Service, EventBridge, budget |
| `deploy/scripts/run` | Single entrypoint for `local` / `hetzner` / `aws`, `up` / `down` |
| `tailor/` | Standalone CLI predecessor of the tailorer agent (local scripts, not wired into the backend) |

`backend/` and `ingestion/` are the two members of a `uv` workspace (Python 3.13).

## API surface

| Endpoint | Notes |
|----------|-------|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | JWT bearer tokens |
| `POST /jobs/`, `GET /jobs/{id}` | Ingestion writes here |
| `POST /companies/`, `GET /companies/{id}` | |
| `POST /jobs/search` | Hybrid search + rerank |
| `POST /jobs/search/advanced`, `.../resume` | Agent search (WIP) |
| `POST /users/cv`, `GET /users/cv` | CV upload for fit scoring / tailoring |
| `GET/PUT /me/preference-memory` | Per-user search preferences |
| `GET/PUT /tailorer/profile`, `WS /tailorer/ws/{job_id}`, `GET /tailorer/files/...` | Tailorer agent + generated documents |
| `GET /health` | Liveness probe for k8s and the ALB |

Most endpoints depend on `get_current_user`.

## Data model

- **`Company`** — unique by `name`; enriched with employee count, founded year,
  review score, financial health score (0–10), consulting/startup flags, industry,
  country.
- **`Job`** — unique by `url`; parsed fields (employment type, location type,
  seniority, required languages) plus a `summary` JSONB.
- **`Outbox`** — transient sync table; `processed_at` is NULL until the worker
  drains it.
- **`User`**, **`ApplicantProfile`**, **`Application`**, **`PreferenceMemory`** —
  accounts, per-user CV data, and the distilled search preferences.

Schema changes go through Alembic in `backend/alembic/`. Compose runs
`alembic upgrade head` at container start; on Kubernetes the bootstrap hook Job
runs it before rollout; on AWS it is the one-shot bootstrap RunTask.

## Key dependencies

| Component | Library |
|-----------|---------|
| API framework | FastAPI + uvicorn |
| ORM / async DB | SQLAlchemy async + asyncpg |
| Vector search | opensearch-py[async] |
| Bi-encoder (embed) | sentence-transformers (`BAAI/bge-small-en-v1.5`) |
| Cross-encoder (rerank) | sentence-transformers (`cross-encoder/ms-marco-MiniLM-L-6-v2`) |
| LLM | Groq SDK; agents use `langchain-openai` against Groq's OpenAI-compatible API |
| Agents / checkpointing | LangGraph + `langgraph-checkpoint-postgres` |
| Auth | python-jose (JWT) + passlib[bcrypt] |
| Scraping | python-jobspy, Playwright, trafilatura, ddgs |
| Infrastructure | OpenTofu, Helm, kube-hetzner (k3s), AWS ECS Fargate |
| Package manager | uv workspace |

## Further reading

- [`deploy/k8s/README.md`](deploy/k8s/README.md) — Kubernetes runbook (cluster, images, secrets)
- [`deploy/infra/aws/README.md`](deploy/infra/aws/README.md) — AWS ECS runbook, IAM policy, DNS cutover
- [`AGENTS.md`](AGENTS.md) — working guide for AI coding agents, and a decent orientation for humans
