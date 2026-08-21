"""Persistent seen-jobs cache — SQLite-backed, avoids re-showing jobs.

Stores job keys (title + company + source) with timestamps so repeated
searches within N days skip already-seen jobs.
"""

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Set

from jobradar.models import Job

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".jobradar", "seen_jobs.db")
DEFAULT_TTL_DAYS = 7


class SeenJobsCache:
    """SQLite-backed cache of previously seen jobs.

    Each entry is keyed by (title, company, source) and stores the
    first-seen timestamp. Jobs older than TTL are automatically pruned.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, ttl_days: int = DEFAULT_TTL_DAYS):
        self.ttl_days = ttl_days
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                title   TEXT NOT NULL,
                company TEXT NOT NULL,
                source  TEXT NOT NULL,
                seen_at REAL NOT NULL,
                PRIMARY KEY (title, company, source)
            )
        """)
        self.conn.commit()

    def _make_key(self, job: Job) -> tuple:
        return (
            job.title.lower().strip(),
            job.company.lower().strip(),
            job.source.lower().strip(),
        )

    def filter_new(self, jobs: List[Job]) -> List[Job]:
        """Return only jobs not seen within the TTL window."""
        self._prune()
        seen = self._get_all_keys()
        new_jobs = []
        for job in jobs:
            key = self._make_key(job)
            if key not in seen:
                new_jobs.append(job)
        return new_jobs

    def mark_seen(self, jobs: List[Job]):
        """Record these jobs as seen (insert or update timestamp)."""
        now = time.time()
        for job in jobs:
            key = self._make_key(job)
            self.conn.execute(
                "INSERT OR REPLACE INTO seen_jobs (title, company, source, seen_at) VALUES (?, ?, ?, ?)",
                (*key, now),
            )
        self.conn.commit()

    def _get_all_keys(self) -> Set[tuple]:
        """Get all non-expired keys."""
        cutoff = time.time() - (self.ttl_days * 86400)
        cursor = self.conn.execute(
            "SELECT title, company, source FROM seen_jobs WHERE seen_at > ?",
            (cutoff,),
        )
        return {(row[0], row[1], row[2]) for row in cursor.fetchall()}

    def _prune(self):
        """Remove entries older than TTL."""
        cutoff = time.time() - (self.ttl_days * 86400)
        self.conn.execute("DELETE FROM seen_jobs WHERE seen_at < ?", (cutoff,))
        self.conn.commit()

    def stats(self) -> dict:
        """Return cache statistics."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM seen_jobs")
        total = cursor.fetchone()[0]
        cutoff = time.time() - (self.ttl_days * 86400)
        cursor = self.conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE seen_at > ?", (cutoff,))
        active = cursor.fetchone()[0]
        return {"total_entries": total, "active_entries": active, "ttl_days": self.ttl_days}

    def clear(self):
        """Clear all cached entries."""
        self.conn.execute("DELETE FROM seen_jobs")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
