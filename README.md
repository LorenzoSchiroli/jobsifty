# Jobsifty

**A job search engine that ranks openings against *you*, not against a keyword.**

Jobsifty continuously scrapes job offers from several sources, parses each
posting into structured metadata with rules, adds a short LLM summary, embeds
it, and serves a hybrid **BM25 + k-NN** search endpoint with **cross-encoder
reranking**. On top of that sits a LangGraph agent, **Agent mode**: it plans
its own searches, loosens what you asked for as little as it can when too few
jobs match, asks before a trade-off you may not accept, and fit-scores 10 jobs
against your request, your CV (optional) and your remembered preferences.

It is a full vertical slice, deliberately: scraping and LLM enrichment →
retrieval and ranking → REST API and SPA → and the same stack deployed two
different ways, on **self-hosted Kubernetes (Hetzner)** and on **managed AWS ECS
Fargate**, from one command.

| | |
|---|---|
| **Retrieval** | OpenSearch hybrid search — BM25 + HNSW k-NN, score-normalised in a single round trip, then reranked by a cross-encoder |
| **Models** | `multilingual-e5-small` (384-d chunk embeddings) + `mmarco-mMiniLMv2-L12` (multilingual rerank), both CPU-only; LLMs only for company judgement fields and offer summaries (a free-tier pool, `llmpool`: NVIDIA NIM, Gemini, Groq) and the agent (the same pool, one `ChatOpenAI` per provider; it sees only the redacted CV) |
| **Durability** | Postgres is the source of truth; the search index is derived and rebuildable, kept in sync by a transactional **outbox** |
| **Agent** | A LangGraph tool-calling loop checkpointed in Postgres — it interrupts, asks, and resumes across HTTP requests |
| **Infra** | OpenTofu + Helm (k3s on Hetzner) *and* OpenTofu + ECS Fargate (AWS), same images, same env contract, `deploy/scripts/run <target> up` |

---

## System overview

```mermaid
flowchart TB
    SRC["Job sources<br/>jobspy · adzuna · arbeitnow · freehire"]
    ING["ingestion · every 2h<br/>scrape → rule parse + LLM summary → embed"]
    CLI["Clients<br/>React SPA"]
    API["backend API<br/>FastAPI · JWT · search · agent"]
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
| `python -m backend.bootstrap` | One-shot. Checkpointer tables + dropping legacy OpenSearch indexes; migrations run alongside it. |

**Ingestion** runs out-of-process as a scheduled batch (k8s CronJob / EventBridge
RunTask): scrape a *scope* (`ai`: one OR query per board where it supports one, a title rule for every job, jobs already stored skipped) → fill missing descriptions with trafilatura → parse each
offer with rules (strip boilerplate sections, regex fields, language detection) → one LLM
summary call (NVIDIA → Gemini; skipped when both fail) → embed ≤4 ~400-token chunks plus the summary → `POST` to the backend, then upsert companies with
what the job boards say about them, and enrich companies until shortly before the next run,
newest jobs' employers first (two web searches each through a pool of free-tier providers — TinyFish, Exa,
Tavily — rotated on rate limits and exhausted credits; JSON-LD/Wikidata/regex first,
one small LLM call on snippets and scraped pages for what is still missing, through
a pool of free LLM providers in the same way).

### Why it is built this way

| Decision | Motivation |
|----------|------------|
| **Postgres is the source of truth, OpenSearch is derived** | A search index is a cache with opinions. Making it authoritative means a lost cluster is lost data, and every schema change becomes a migration you cannot roll back. Here the index can be dropped and rebuilt from Postgres at any time. |
| **Transactional outbox instead of dual writes** | Writing to Postgres *and* OpenSearch in one request means either a distributed transaction or silent divergence when the second write fails. The job and its `outbox` row are committed atomically; the worker drains the outbox and additionally re-indexes any live job (posted within `JOB_LIVE_DAYS`, 14 days by default) missing from the index, so the system self-heals instead of drifting. |
| **Ingestion talks to the public REST API, not to the database** | The pipeline holds no DB credentials and no ORM models. It is just another client, which is what lets it run as a CronJob on Kubernetes or a Fargate task on AWS with zero extra wiring — and what keeps validation in exactly one place. |
| **LLMs only offline and on the opt-in agent path** | Summarising a posting is a batch job where a 2-second LLM call is free; a search request is not. The hot search path is regex + two small local models, so it is fast, cheap, deterministic and unit-testable. |
| **CPU-only models** | `multilingual-e5-small` (384-d) and `mmarco-mMiniLMv2-L12` were benchmarked on CPU (e5-small embeds a 512-token chunk in ~57 ms, same as the previous `bge-small`; long-context and 0.5B+ models were 10–500× slower) and chosen to fit a demo budget with no GPU anywhere in the stack — the whole thing runs on a single small Hetzner x86 VPS and on `db.t4g.micro` / `t3.small.search`-class AWS resources. |
| **uv workspace, three Python packages** | `backend`, `ingestion` and the shared `llmpool` share a lockfile and Python version but ship as separate images with separate dependency sets — the API image does not carry Playwright, the scraper does not carry SQLAlchemy models. |

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
    E["2 · embed<br/>multilingual-e5-small → 384-d"]
    H["3 · hybrid retrieve<br/>OpenSearch: BM25 + k-NN, fused"]
    R["4 · rerank<br/>cross-encoder → top 20"]
    D["5 · hydrate<br/>Postgres, by job_id → {filters, results}"]

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
| **Filters are *soft* by default** | The filterable fields (`seniority`, `location_type`, `languages_required`, …) come from a rule-based parse of free-text postings, so they are good but not perfect. A hard filter turns one bad extraction into a silently missing result. Instead the clauses are scored `should` clauses with `boost: 2.0` — matching documents win, near-misses still surface. Writing *"strictly"*, *"exactly"* or *"no exceptions"* flips `SearchFilters.strict`, which applies them as a real `post_filter` and raises the prefetch to 200 so the filter has candidates left to keep. |
| **Places are where the job is, in both modes** | *"In Munich"* means the job is in Munich, not that the company is based in Germany: a Munich job at a Serbian company is a Munich job. Each job's cities and countries are read from its location at indexing ("München, BY", "Greater Munich Area" and "Munich" are one city), and basic search and the agent filter on them alike, several at once (*"in Munich or Austria"*). The company's country is a filter only when asked for (*"company based in Germany"*). |
| **The age window is always hard, 72 hours by default** | A posting a week old is usually already being interviewed for, so old jobs are left out rather than ranked lower. The window is on `posted_at` (the board's date, `created_at` when it gives none) and the query text changes it: *"last 2 weeks"*, *"today"*, *"this week"*. On the k-NN leg the date is the knn query's own filter on `chunks.posted_at` (each chunk carries the job's date), so the nearest neighbours are searched among recent jobs: a filter around the k-NN result would keep only the recent few of its top 100. |
| **Cross-encoder rerank on the shortlist only** | The bi-encoder scores query and document independently — necessary, because embeddings must be computed at index time. A cross-encoder reads the pair jointly and is markedly more accurate, but costs one forward pass *per candidate*. Applying it to the top 20–200 retrieved hits buys most of the accuracy for a bounded, predictable cost. |
| **Chunks plus an optional LLM summary** | Descriptions run ~1k tokens (p99 ~2.7k), twice the 512-token window of small CPU encoders. Boilerplate sections are stripped by rules and the rest is embedded as ≤4 overlapping chunks, so every job is searchable whatever the LLM does. An LLM summary (from free-tier providers, never Groq's quota) is added as one more chunk when a provider answers: on a blind comparison (8 queries, 732 jobs, 2026-10) the plain chunks ranked worse than the old summary-only index (35 against 44), chunks plus the summary better (45), and only at full coverage: summaries on 45% of the jobs scored 36. k-NN scores a job by its best chunk; the reranker reads the summary, else that best chunk. |
| **384 dimensions, HNSW + cosine on the Lucene engine** | Small vectors keep index size and HNSW memory low, `multilingual-e5-small` covers the non-English offers, and the Lucene engine needs no native plugin and supports filtered k-NN. |
| **A `parse=… embed=… retrieve=… rerank=… db=…` timing line per query** | Each stage is logged separately, so a latency regression points at a stage instead of at "search is slow". |

### Advanced search — a tool-calling LangGraph agent (WIP)

`POST /jobs/search/advanced` → optionally `POST /jobs/search/advanced/resume`
with the answer to a question. The UI calls this mode **Agent**. The CV is
optional: without one the agent works from the request and the preference
memory, and choosing Agent on the search page suggests adding one. On the `/cv`
page you upload a file (PDF/DOCX/TXT, text extracted and not stored) or write
the CV; "Remove private info" (`backend/cv/redact.py`) replaces obvious personal
data with placeholders such as `[EMAIL]` and `[PHONE]`, and `PUT /users/cv`
answers 409 with the findings until you confirm (saving empty text deletes the
CV). LLM calls see only the saved text. The preference memory is read and edited
on the `/preferences` page. Basic search uses neither the CV nor the preference
memory.

The agent calls the LLM through `langchain-openai` `ChatOpenAI`, one per provider
of the shared `llmpool` pool (`LLM_PROVIDERS` order, Groq on `GROQ_MODEL_LARGE`).
A 429, a server error or a timeout moves the call to the next provider (Groq's
`tool_use_failed` / `json_validate_failed` is first retried twice on Groq), a
bad key puts the provider out for an hour (an unknown model first gives way to
its next one), and a provider paused by one request is skipped by the next.
Each attempt gets a share of the call's remaining time, so a slow provider is
cut while a fallback still fits.

```mermaid
flowchart TB
    A["POST /jobs/search/advanced<br/>query · CV and memory in the run context"]
    L["agent turn<br/>one tool call"]
    T["update_brief · inspect_job<br/>search_jobs — 1–3 variants, may loosen<br/>stated items for that one search"]
    Q["ask_user — interrupt()<br/>question + options returned"]
    G["gather_closest<br/>one soft-filter search<br/>when fewer than 10 jobs"]
    F["finalize<br/>fit, gaps, trade-offs, misses<br/>over the whole pool"]
    P["partial<br/>no provider answered or<br/>unparsed tool call"]
    R["10 jobs<br/>matches first, then the closest,<br/>each with what it misses"]
    M[("preference memory")]

    A --> L
    L --> T --> L
    L --> Q
    Q -->|"POST /resume<br/>thread_id + answer"| L
    L -->|"finish, budget spent<br/>or time up"| G --> F --> R
    L -->|"LLM failure"| P --> R
    F -->|"background: distil"| M
    M -.->|"next search"| A
```

*Not drawn:* `partial` also runs `gather_closest`, and a failed or timed-out
`finalize` ends `partial` too (without fit scores or a summary); the Stop button
aborts the request and the server cancels the run.

| Choice | Motivation |
|--------|------------|
| **A tool loop, one call per turn** | The model decides what to do next (update what it understood, search with 1–3 query variants and, if it judges so, a loosened requirement, read a job, ask, finish) instead of following a fixed graph. One call per turn keeps every step observable and the budget countable. |
| **Stated vs inferred requirements** | What you typed is locked in the brief and every result must satisfy it; only you can relax it, by answering a question. A requirement that maps to an index field uses the index's own values (the tool schema lists them), so "on-site" or "English" filters what ingestion wrote rather than matching nothing. One no field carries ("an NLP or search role") only the LLM can judge, so the final step marks each job that misses it and code moves those jobs out of the matches: the model cannot note "no NLP focus" and still shortlist the job. What the agent inferred from your CV and past searches it may trade, and so is any preference ("preferably remote"): it ranks but is never locked, so results can include non-remote jobs. "In Munich" filters on where the job is (the index reads each job's city and country from its location, "München, BY" included), not where the company is based: a Munich job at a Serbian company is a Munich job. |
| **A 3-day window (or the remembered one), widened as far as the agent judges** | An older posting has more applicants already, so a CV sent to it lands at the back of the queue. The agent starts at 72 hours, or at the window the preference memory states ("jobs from the last 2 weeks" starts at 2 weeks), and widens when results are too few or unsatisfying, by as much as it judges, up to `JOB_LIVE_DAYS` (the index holds nothing older); its notes tell the user. Fixed steps (3 → 7 → 14 days) were tried and dropped: how far to go depends on the request, which the agent reads and a ladder does not. A remembered window is a preference: no filter. A window the user states ("last 2 weeks"), which wins over a remembered one, is a stated requirement: searches use it, and the older jobs a wider search finds fill the result among the closest jobs, never as matches. |
| **A hard budget: 6 turns, 9 searches, 2 questions, 4 minutes** | An agent loop is how a 3-second search becomes a 40-second one with an unbounded LLM bill. A spent budget goes straight to `finalize` and ends with a normal shortlist; every LLM call is cut at the deadline (45 s kept for finalize) and capped in output tokens. The answer is `partial` only when the assistant could not finish (no LLM provider answered, an unparsed answer, a failed or timed-out finalize): the most relevant jobs found, without fit scores or a summary. |
| **Always 10 jobs: the matches, then the closest ones** | The result is a ranking of what comes closest to the request, the CV and the preferences: the request is strict, the CV and preferences only rank. A strict request can leave few matches, and an empty or short page tells the user less than the near-misses would, so the list always fills up to 10: the jobs meeting every stated requirement first, then a second section, "Closest jobs", each saying what it misses in the filters panel's words ("Misses: remote", "remote (unknown)" when the posting does not say, "an NLP role" when the model judged it), fewest misses first. To find them the agent loosens requirements in single searches, as it judges and as little as it can: a nearby place before a far one (Munich: Augsburg, Austria and Switzerland, German-speaking and close, before Hamburg), a wider window, another value of any field; a loosened search never changes the requirements, so its jobs never count as matches. When the obvious loosenings do not help, or the only way is a change you may not accept (giving up remote), it asks. If the pool is still short, code runs one more search with the requirements as soft filters, as basic search applies them, and `finalize` scores those jobs with the rest. `finalize` gives a verdict on every job, one entry each: left free to pick its best few, the model dropped the jobs that missed a requirement only it could judge, so they came back without a score and with a miss nobody had confirmed. |
| **`finalize` scores the whole pool** | Fit score, rationale, gaps and trade-offs are written once over every job found, not only the last search, so an early good hit is not lost. What a job's fields prove against the brief's inferred preferences is added by code, so the model cannot talk it away: a language the CV does not list is a gap (it requires Spanish, the CV lists Italian, English and German), another work mode, seniority or company type a trade-off. |
| **State in Postgres only while a question is open** | The agent interrupts to ask, so the run survives an HTTP round trip: checkpointed by `thread_id` (`durability="exit"`), `/resume` can land on any API replica. The thread is deleted when a run ends. The CV stays in the run context and out of the checkpoint. |
| **The shared provider pool, not one LLM** | Free tiers rate-limit and gpt-oss sometimes emits a malformed tool call, so one provider would end many runs `partial`. The agent tries the `llmpool` providers in `LLM_PROVIDERS` order with the pool's own pause state; since the CV is redacted before it is saved, any provider may see it. |
| **Preference memory is distilled in a background task** | The user gets results at once; the LLM that merges this session into their long-term preference summary runs after the response. A `user_edited` flag protects a hand-written summary: the distiller may only append. |

While a search runs, basic or agent, the page shows a spinner and the step it
is on ("Searching "NLP engineer" · last 3 days", "Scoring the jobs found…"),
each step replacing the last: asked with `Accept: application/x-ndjson`, the
search endpoints answer one JSON line per step, then the result
(`backend/progress.py`; the agent's steps come from LangGraph's custom stream).
Without that header they answer plain JSON as before. The search button turns into Stop
meanwhile: it closes the request, and the server cancels the work when its client
is gone, so a stopped agent run makes no more LLM calls.

Above the results both modes list the active filters the same way: **required**
(every result matches) and **preferred** (ranked higher, not required). Basic
search's soft filters are preferred and its posting window required (all of
them required with "strictly"); in agent mode what you stated is required, in
the same words basic search uses ("remote", "no consulting"), with the window
the run used, and what the agent inferred is preferred.

Not built yet: one active run per user and the sweep of abandoned threads.

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
with unprocessed outbox rows, plus any live job (posted within `JOB_LIVE_DAYS`, default 14) missing from the
index. `company_upserted` events patch company-derived fields across that
company's job documents with a single `update_by_query`. Every 6 hours retention
drops OpenSearch documents posted more than `JOB_LIVE_DAYS` ago, keeping the index at "what you
could actually still apply to". With `BACKUP_SBOX_*` set, the worker also runs a
daily `pg_dump` + rclone upload to a Hetzner Storage Box on a 7-day rolling
retention.

The `jobs_v4` index carries the nested `chunks` (text + embedding + the job's posted date; the reranker
reads the summary chunk, else re-picks the best chunk from them, since hybrid queries drop `inner_hits`), `description` for BM25, `company_name` for the advanced-search prompts, and the keyword/numeric
fields the filter clauses target — `employment_type`, `location_type`,
`seniority`, `languages_required`, `is_consulting`, `is_startup`, `industry`,
`country` (the company's), `job_cities` / `job_countries` (where the job is, read from its location),
`review_score`, `financial_health_score`, `created_at`, `posted_at`.

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
| `GROQ_API_KEY` | `.env` | Last in the company enrichment and advanced-search pools |
| `NVIDIA_NIM_API_KEY` / `GEMINI_API_KEY` | `.env` | Offer summary, company enrichment and advanced-search LLM pools. Gemini's free tier trains on prompts; every provider is assumed to keep them, so the CV is cleaned before it is saved (`/cv` page) |
| `SECRET_KEY` | `.env` | JWT signing |
| `INGEST_TOKEN` | `.env` | Ingestion's job/company writes (`X-Ingest-Token`); refused while unset |
| `TINYFISH_API_KEY` / `EXA_API_KEY` / `TAVILY_API_KEY` | `.env` | Company enrichment web search pool; any subset, missing keys are skipped |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | `.env` | Optional job source |
| `BACKUP_SBOX_HOST` / `_USER` / `_RCLONE_PASS` | `.env` | Optional nightly Postgres backup |
| `GROQ_MODEL_LARGE` / `GROQ_MODEL_BASE` | `.env.public` | Groq's model in the advanced-search agent / in company enrichment |
| `NVIDIA_MODEL` / `GEMINI_MODEL` / `LLM_PROVIDERS` | `.env.public` | Company enrichment and advanced-search LLM pool: models (comma-separated fallbacks) and order (default `nvidia,gemini,groq`) |
| `OFFER_LLM_PROVIDERS` | `.env.public` | Offer summary LLM pool order (default `nvidia,gemini`, Groq left out) |
| `JOB_LIVE_DAYS` | `.env.public` | Days a job stays live, from its posting time: search retention, reconcile, company enrichment (default 14) |
| `SEARCH_PROVIDERS` | `.env.public` | Search pool order (default `tinyfish,exa,tavily`) |
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
uv run python -m ingestion.pipeline ai --hours 48
uv run pytest
```

Ad-hoc CLIs for debugging a single stage:

```bash
uv run python -m ingestion.offer ai --hours 48 --sources jobspy,adzuna
uv run python -m ingestion.company "Stripe" "San Francisco" --debug
```

Offer sources: `jobspy`, `adzuna`, `arbeitnow`, `freehire` (default: all); what each can do, and why others such as Remotive were dropped, is in [`ingestion/SOURCES.md`](ingestion/SOURCES.md).

## Repository layout

| Path | What it is |
|------|------------|
| `backend/` | FastAPI service — REST API, Postgres (SQLAlchemy async), OpenSearch hybrid search, outbox worker, LangGraph agent |
| `llmpool/` | Shared provider pool and LLM chat providers (ingestion's LLM calls; the agent's providers and their pause state) |
| `ingestion/` | Pipeline — job scraping, rule-based offer parsing, company enrichment, chunked embedding, POST to the backend |
| `frontend/` | React (Vite) SPA, served by nginx in Docker/k8s |
| `deploy/helm/` | Helm chart for the whole stack + `values-local` / `values-cloud` / `values-hetzner` |
| `deploy/infra/hetzner/` | OpenTofu — kube-hetzner k3s cluster + Cloudflare records |
| `deploy/infra/aws/` | OpenTofu — VPC, ALB, ECS Fargate, RDS, OpenSearch Service, EventBridge, budget |
| `deploy/scripts/run` | Single entrypoint for `local` / `hetzner` / `aws`, `up` / `down` |
| `tailor/` | Standalone CV-tailoring CLI scripts (gitignored, local only, not wired into the backend) |

`backend/`, `ingestion/` and `llmpool/` are the members of a `uv` workspace (Python 3.13).

## API surface

| Endpoint | Notes |
|----------|-------|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | JWT bearer tokens |
| `POST /jobs/`, `GET /jobs/`, `GET /jobs/stored`, `GET /jobs/{id}` | Ingestion writes here (`X-Ingest-Token`); a paged list (the backfill reads it) and the live jobs' URL/title/company, which ingestion skips |
| `POST /companies/`, `GET /companies/pending`, `GET /companies/{id}` | Ingestion writes here (`X-Ingest-Token`); the companies due for enrichment |
| `POST /jobs/search` | Hybrid search + rerank, `{filters, results}` |
| `POST /jobs/search/advanced`, `.../resume` | Agent search (WIP): results (`done` / `partial`) or a question (`question`, `thread_id`) |
| `POST /users/cv/extract`, `POST /users/cv/redact`, `PUT /users/cv`, `GET /users/cv` | CV text from a file, private-info removal, save with a confirm check (empty text deletes it), read |
| `GET/PUT /me/preference-memory` | Per-user search preferences |
| `Accept: application/x-ndjson` | On both searches: one JSON line per step, then the result (the progress line and Stop in the UI) |
| `GET /health` | Liveness probe for k8s and the ALB |

Most endpoints depend on `get_current_user`.

## Data model

- **`Company`** — unique by `name`; enriched with employee count, founded year,
  review score, financial health score (1–5, only with evidence), consulting/startup
  flags, industry, country; `enriched_at` / `enrich_attempts` pace the enrichment retries, and data over a year old is re-enriched and replaced.
- **`Job`** — unique by `url`; parsed fields (employment type, location type,
  seniority, required languages) plus `chunks` JSONB (text + embedding each; the LLM summary chunk is flagged `summary`).
- **`Outbox`** — transient sync table; `processed_at` is NULL until the worker
  drains it.
- **`User`**, **`ApplicantProfile`**, **`PreferenceMemory`** —
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
| Bi-encoder (embed) | sentence-transformers (`intfloat/multilingual-e5-small`) |
| Cross-encoder (rerank) | sentence-transformers (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) |
| LLM | Ingestion: `llmpool` OpenAI-compatible pool (NVIDIA NIM / Gemini / Groq); agent: one `langchain-openai` `ChatOpenAI` per `llmpool` provider, tried in `LLM_PROVIDERS` order |
| Agents / checkpointing | LangGraph + `langgraph-checkpoint-postgres` |
| Auth | python-jose (JWT) + passlib[bcrypt] |
| Scraping | python-jobspy, Playwright, trafilatura |
| Company web search | TinyFish / Exa / Tavily REST APIs (provider pool) |
| Infrastructure | OpenTofu, Helm, kube-hetzner (k3s), AWS ECS Fargate |
| Package manager | uv workspace |

## Further reading

- [`deploy/k8s/README.md`](deploy/k8s/README.md) — Kubernetes runbook (cluster, images, secrets)
- [`deploy/infra/aws/README.md`](deploy/infra/aws/README.md) — AWS ECS runbook, IAM policy, DNS cutover
- [`AGENTS.md`](AGENTS.md) — working guide for AI coding agents, and a decent orientation for humans
