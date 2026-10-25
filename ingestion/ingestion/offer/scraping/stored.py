"""The live jobs the backend already has, so a run skips them before any work.

A job is stored when its URL is, or when its title + company is (the same job reposted,
or listed under another URL by another board): the key the in-run dedup uses. The
stored job is left as it is.
"""
from dataclasses import dataclass

from ingestion.offer.scraping.deduplicator import job_key


@dataclass(frozen=True)
class StoredJobs:
    urls: frozenset[str] = frozenset()
    keys: frozenset[tuple[str, str]] = frozenset()

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "StoredJobs":
        """From `GET /jobs/stored`: [{url, title, company_name}]."""
        return cls(
            urls=frozenset(r["url"] for r in rows),
            keys=frozenset(job_key(r["title"], r["company_name"]) for r in rows),
        )

    def same_url(self, url: str) -> bool:
        return url in self.urls

    def same_job(self, title: str, company: str) -> bool:
        return job_key(title, company) in self.keys

    def __len__(self) -> int:
        return len(self.urls)
