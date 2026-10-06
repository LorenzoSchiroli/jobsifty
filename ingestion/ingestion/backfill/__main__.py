"""Re-derive every stored job with the current parser and embedder.

Pages through GET /jobs/, rebuilds fields and chunks locally (no LLM)
and POSTs them back; a post with a description makes the backend overwrite the
previously derived fields.

    uv run python -m ingestion.backfill [--limit-pages N] [--dry-run]
"""
import argparse
import logging
import sys
from datetime import datetime

from tqdm import tqdm

from ingestion.client import list_jobs, post_job
from ingestion.offer.offer import derive
from ingestion.offer.scraping.models import JobOffer

logger = logging.getLogger(__name__)


def _to_offer(item: dict) -> JobOffer:
    posted_at = item.get("posted_at")
    return JobOffer(
        title=item["title"],
        company=item["company_name"],
        location=item.get("location") or "",
        url=item["url"],
        source=item.get("source") or "",
        posted_at=datetime.fromisoformat(posted_at) if posted_at else None,
        description=item.get("description"),
    )


def run(limit_pages: int | None = None, dry_run: bool = False, page_size: int = 500) -> dict:
    counts = {"seen": 0, "posted": 0, "errors": 0}
    after: str | None = None
    pages = 0
    with tqdm(desc="Backfill", unit="job") as bar:
        while True:
            items, after = list_jobs(after=after, limit=page_size)
            pages += 1
            for item in items:
                counts["seen"] += 1
                bar.update(1)
                try:
                    enriched = derive(_to_offer(item))
                    if not dry_run:
                        post_job(enriched)
                    counts["posted"] += 1
                except Exception as e:
                    counts["errors"] += 1
                    logger.warning("Backfill failed for %s: %s", item.get("url"), e)
            if not after or not items or (limit_pages is not None and pages >= limit_pages):
                break
    logger.info("Backfill done: %d seen, %d %s, %d errors", counts["seen"], counts["posted"],
                "derived (dry run)" if dry_run else "posted", counts["errors"])
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    parser = argparse.ArgumentParser(description="Re-derive and re-post all stored jobs")
    parser.add_argument("--limit-pages", type=int, default=None, help="Stop after N pages of 500 jobs")
    parser.add_argument("--dry-run", action="store_true", help="Derive but do not POST")
    args = parser.parse_args()
    counts = run(limit_pages=args.limit_pages, dry_run=args.dry_run)
    sys.exit(1 if counts["seen"] and counts["errors"] / counts["seen"] > 0.2 else 0)


if __name__ == "__main__":
    main()
