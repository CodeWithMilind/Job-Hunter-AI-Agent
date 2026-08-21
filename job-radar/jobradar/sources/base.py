"""Base types for pluggable JobRadar job sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional

from jobradar.models import Job


@dataclass
class SourceFetchResult:
    """The isolated result of one source fetch."""

    source_id: str
    jobs: List[Job] = field(default_factory=list)
    error: str = ""
    fetched_at: float = field(default_factory=time.time)


class JobSource(ABC):
    """Common interface implemented by all supported job resources."""

    source_id: str
    name: str
    source_type: str
    enabled: bool
    metadata: Dict[str, Any]

    @abstractmethod
    def fetch(self, query: str, *, location: str = "", limit: int = 50, max_pages: int = 3, companies_path: str = "companies.yaml") -> List[Job]:
        """Fetch matching jobs. Implementations may raise; use fetch_safely in pipelines."""

    def fetch_safely(self, query: str, **kwargs: Any) -> SourceFetchResult:
        """Fetch one source without allowing its failure to stop other sources."""
        try:
            jobs = self.fetch(query, **kwargs)
            for job in jobs:
                job.source = self.name
                job.source_type = self.source_type
                job.original_url = job.original_url or job.url
                job.source_metadata = {**self.metadata, **job.source_metadata}
            return SourceFetchResult(source_id=self.source_id, jobs=jobs)
        except Exception as exc:
            return SourceFetchResult(source_id=self.source_id, error=str(exc))
