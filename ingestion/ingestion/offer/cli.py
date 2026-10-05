import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from tabulate import tabulate

from ingestion.offer.offer import enrich_all
from ingestion.offer.scraping.sources.adzuna_source import AdzunaSource
from ingestion.offer.scraping.sources.arbeitnow_source import ArbeitnowSource
from ingestion.offer.scraping.sources.jobspy_source import JobspySource
from ingestion.offer.scraping.sources.remotive_source import RemotiveSource

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("offer").setLevel(logging.INFO)

_ALL_SOURCES = {
    "jobspy": JobspySource,
    "adzuna": AdzunaSource,
    "arbeitnow": ArbeitnowSource,
    "remotive": RemotiveSource,
}


def _t(value: str | None, n: int) -> str:
    if not value:
        return "—"
    return value if len(value) <= n else value[: n - 1] + "…"


def main() -> None:
    load_dotenv(".env.public")
    load_dotenv(".env", override=True)

    parser = argparse.ArgumentParser(description="Fetch and parse job offers.")
    parser.add_argument("query", help="Search query, e.g. 'machine learning engineer'")
    parser.add_argument("--hours", type=int, default=72, help="How many hours back to search (default: 72)")
    parser.add_argument(
        "--sources",
        default=",".join(_ALL_SOURCES),
        help=f"Comma-separated sources. Available: {', '.join(_ALL_SOURCES)} (default: all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Save full results (including description and parsed fields) to data/offers.json",
    )
    args = parser.parse_args()

    source_names = [s.strip() for s in args.sources.split(",")]
    unknown = [s for s in source_names if s not in _ALL_SOURCES]
    if unknown:
        parser.error(f"Unknown sources: {', '.join(unknown)}. Available: {', '.join(_ALL_SOURCES)}")

    sources = [_ALL_SOURCES[name]() for name in source_names]

    print(f"Searching and parsing '{args.query}' (last {args.hours}h)...\n")

    results = enrich_all(args.query, args.hours, sources)

    if not results:
        print("No offers found.")
        return

    rows = [
        [
            i + 1,
            _t(o.title, 40),
            _t(o.company, 20),
            _t(o.employment_type, 12),
            _t(o.location_type, 8),
            _t(o.office, 15),
            _t(o.seniority, 10),
            _t(o.salary_range, 20),
            (o.posted_at.strftime("%Y-%m-%d %H:%M") if o.posted_at.hour or o.posted_at.minute else o.posted_at.strftime("%Y-%m-%d")) if o.posted_at else "—",
        ]
        for i, o in enumerate(results)
    ]

    headers = ["#", "Title", "Company", "Type", "Location", "Office", "Seniority", "Salary", "Posted"]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print(f"\n{len(results)} offers found.")

    if args.json:
        out_path = Path("data/offers.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                [o.model_dump(mode="json") for o in results],
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"Saved to {out_path}")
