"""Remotive.com job source (free, no auth)."""

import logging
from typing import List

import requests

from hirevia.models import Job

logger = logging.getLogger(__name__)


class RemotiveSearch:
    """Search jobs via Remotive.com API (free, no auth).

    Note: Remotive does not support server-side pagination — the API returns
    up to ~1000 matching jobs in a single response.  We use the ``limit``
    parameter to cap results.
    """

    BASE = "https://remotive.com/api/remote-jobs"
    SOURCE = "Remotive"
    last_error = ""

    @staticmethod
    def search(query: str, limit: int = 50, max_pages: int = 3) -> List[Job]:
        RemotiveSearch.last_error = ""
        try:
            resp = requests.get(
                RemotiveSearch.BASE,
                params={"search": query, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for item in data.get("jobs", [])[:limit]:
                salary = ""
                if item.get("salary"):
                    salary = item["salary"]
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("candidate_required_location", "Anywhere"),
                    url=item.get("url", ""),
                    description=(item.get("description") or "")[:500],
                    salary=salary,
                    source=RemotiveSearch.SOURCE,
                    source_metadata={"source_job_id": str(item.get("id"))} if item.get("id") else {},
                    remote=True,
                    country=item.get("country", ""),
                    timezone=item.get("timezone", ""),
                    tags=item.get("tags", []),
                    posted=item.get("publication_date", ""),
                ))
            return jobs
        except Exception as e:
            RemotiveSearch.last_error = str(e)
            logger.warning("Remotive search failed: %s", e)
            return []
