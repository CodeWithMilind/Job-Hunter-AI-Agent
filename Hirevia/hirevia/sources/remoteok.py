"""RemoteOK job source (free, no auth, no server-side pagination).

The RemoteOK API returns up to ~100 recent jobs in a single JSON array.
The first element is metadata; subsequent elements are job objects.
"""

import logging
from typing import List

import requests

from hirevia.models import Job

logger = logging.getLogger(__name__)


class RemoteOKSearch:
    """Search jobs via RemoteOK.com API (free, no auth).

    Note: RemoteOK does not support server-side pagination or search queries.
    The API always returns the same ~100 most-recent remote jobs.  We filter
    client-side by the query terms.
    """

    BASE = "https://remoteok.com/api"
    SOURCE = "RemoteOK"
    last_error = ""

    @staticmethod
    def search(query: str, limit: int = 50, max_pages: int = 3) -> List[Job]:
        RemoteOKSearch.last_error = ""
        try:
            resp = requests.get(
                RemoteOKSearch.BASE,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or len(data) < 2:
                return []

            # First element is metadata; rest are jobs
            query_words = set(query.lower().split())
            jobs = []
            for item in data[1:]:
                if not isinstance(item, dict):
                    continue
                # Client-side relevance filter
                position = (item.get("position") or "").lower()
                company = (item.get("company") or "").lower()
                tags = [t.lower() for t in (item.get("tags") or [])]
                searchable = f"{position} {company} {' '.join(tags)}"
                if not any(w in searchable for w in query_words):
                    continue

                salary_min = item.get("salary_min") or 0
                salary_max = item.get("salary_max") or 0
                salary = ""
                if salary_min and salary_max:
                    salary = f"${salary_min:,}-${salary_max:,}"
                elif salary_min:
                    salary = f"${salary_min:,}+"

                jobs.append(Job(
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    url=item.get("url") or item.get("apply_url") or "",
                    description=(item.get("description") or "")[:500],
                    salary=salary,
                    source=RemoteOKSearch.SOURCE,
                    source_metadata={"source_job_id": str(item.get("id") or item.get("slug"))} if (item.get("id") or item.get("slug")) else {},
                    remote=True,
                    country=item.get("country", ""),
                    timezone=item.get("timezone", ""),
                    tags=item.get("tags", []),
                    posted=item.get("date", ""),
                ))
                if len(jobs) >= limit:
                    break

            return jobs
        except Exception as e:
            RemoteOKSearch.last_error = str(e)
            logger.warning("RemoteOK search failed: %s", e)
            return []
