import argparse
import logging
import os

from dotenv import load_dotenv
from tabulate import tabulate

from ingestion.offer.scraping.scraping import scrape
from ingestion.offer.scraping.sources.adzuna_source import AdzunaSource
from ingestion.offer.scraping.sources.arbeitnow_source import ArbeitnowSource
from ingestion.offer.scraping.sources.freehire_source import FreehireSource
from ingestion.offer.scraping.sources.jobspy_source import JobspySource
from ingestion.offer.scraping.sources.remotive_source import RemotiveSource

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("offer").setLevel(logging.INFO)

_ALL_SOURCES = {
    "jobspy": JobspySource,
    "adzuna": AdzunaSource,
    "arbeitnow": ArbeitnowSource,
    "freehire": FreehireSource,
    "remotive": RemotiveSource,
}


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def main() -> None:
    load_dotenv(".env.public")
    load_dotenv(".env", override=True)

    parser = argparse.ArgumentParser(description="Fetch recent job offers matching a query.")
    parser.add_argument("query", help="Search query, e.g. 'machine learning engineer'")
    parser.add_argument("--hours", type=int, default=72, help="How many hours back to search (default: 72)")
    parser.add_argument(
        "--sources",
        default=",".join(_ALL_SOURCES),
        help=f"Comma-separated sources to use. Available: {', '.join(_ALL_SOURCES)} (default: all)",
    )
    parser.add_argument("--descriptions", action="store_true", help="Print full description for each offer")
    args = parser.parse_args()

    source_names = [s.strip() for s in args.sources.split(",")]
    unknown = [s for s in source_names if s not in _ALL_SOURCES]
    if unknown:
        parser.error(f"Unknown sources: {', '.join(unknown)}. Available: {', '.join(_ALL_SOURCES)}")

    sources = [_ALL_SOURCES[name]() for name in source_names]

    print(f"Searching for '{args.query}' (last {args.hours}h) across: {', '.join(source_names)}...\n")

    deduped = scrape(args.query, args.hours, sources=sources)

    if not deduped:
        print("No offers found.")
        return

    rows = [
        [
            i + 1,
            _truncate(o.title, 45),
            _truncate(o.company, 25),
            _truncate(o.location, 25),
            o.source,
            str(o.posted_at) if o.posted_at else "—",
            _truncate(o.url, 60),
        ]
        for i, o in enumerate(deduped)
    ]

    headers = ["#", "Title", "Company", "Location", "Source", "Posted", "URL"]
    print(tabulate(rows, headers=headers, tablefmt="simple"))
    print(f"\n{len(deduped)} offers found.")

    if args.descriptions:
        print()
        for i, o in enumerate(deduped):
            print(f"--- [{i + 1}] {o.title} ({o.company}) ---")
            print(o.description or "(no description)")
            print()
