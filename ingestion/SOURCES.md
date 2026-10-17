# Job sources

Every source we use or have evaluated, what it can and cannot do, and why it is in or
out. Add to it when a source is tried, changed or dropped, with the date and the numbers
that decided it. The code is in `ingestion/offer/scraping/sources/`; what a run searches
for is the scope in `ingestion/offer/scraping/scopes.py`.

Every source gets the scope's terms in the form it understands, and every job it returns
must then have a Latin-script title and pass the scope's title rule, whatever the source. Jobs whose URL is already
stored are skipped before their description is fetched.

## In use

| Source | Access | Query | Finest age filter | Per run | Notes |
|--------|--------|-------|-------------------|---------|-------|
| LinkedIn (jobspy) | Guest pages, no key | One OR query, location "Europe" | Hours (`f_TPR`) | 150 cards | Hundreds of AI jobs in Europe every 4 h, far beyond the cap. Each kept card costs one more request (its description); off-scope and stored cards are skipped on the card, before it. 429s under load. "N hours ago" on the card gives the time |
| Indeed (jobspy) | GraphQL, no key | One OR query, 8 countries | Hours | 20 per country | Matches descriptions too, so most results fail the title rule: about 1 AI job per country every 4 h. Cheap, so kept. Exact timestamp (`dateOnIndeed`) |
| Glassdoor (jobspy) | No key | One OR query, 4 countries (needs "United Kingdom", not "UK") | Days only | 25 per country | OR doubles the raw results but barely the relevant ones. Day only, stored as midnight |
| Adzuna | Free key, 250 requests/day | `what_or` with the terms, newest first, 8 countries | Days at the API; hours cut locally on its exact `created` | 50 per country | One request per country per run (96/day at a 2 h interval). Matches descriptions: ~16% of results pass the title rule. Description is a snippet, the page is fetched |
| freehire.me | Public, keyless JSON API; robots.txt allows it | One search per term (no OR: a multi-word query requires its first word) | Hours, on `created_at` (when freehire found the job) | Pages of 50 until the window | Aggregates employers' ATS boards (Greenhouse, Lever, Ashby, Workday…), EURES and the Bundesagentur, with full descriptions. A quarter of jobs are found 5+ hours after posting, hence the window on `created_at`. Skips its `stale` (re-posted) and `likely-evergreen` postings. 2026-10: ~45 new EU ML jobs a day, 96% not in our DB |
| Arbeitnow | Public, keyless API | None (no search): the feed, filtered by title | Hours (`created_at`) | 3 pages of ~300 | Behind Cloudflare: a burst of requests gets 429 "Just a moment…", so pages are 3 s apart. Pages are not strictly newest first. 2026-10: 59 AI/ML jobs in 3 days, 34 of them not in our DB |

## Evaluated and not used

| Source | Decided | Why not |
|--------|---------|---------|
| Remotive | 2026-10-17, removed | The public API returns the same 13 jobs whatever the parameters (`limit`, `category`, `search`), delayed by 24 hours, and asks for at most 4 requests a day. Full access is a paid API (from $5k/month). Its terms forbid showing its jobs behind a sign-up. It brought 0 jobs in every run |
| Relocate.me | 2026-10-17 | No API or feed (HTML only, though each job page has JobPosting JSON-LD and robots.txt allows it). Only ~26 open jobs on the whole board, mostly in Japan, ~3 EU AI roles, from employers freehire already covers. Day-only dates |
| Greenhouse / Lever / Ashby boards directly | 2026-10 | freehire already aggregates these ATS boards with full descriptions. Worth building only as a fallback if freehire goes away |
| Arbeitsagentur / EURES directly | 2026-10 | Covered by freehire |
