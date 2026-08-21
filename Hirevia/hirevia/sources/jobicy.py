"""Jobicy job source (free, no auth, paginated via v2 API)."""

import logging
from typing import List

import requests

from hirevia.models import Job

logger = logging.getLogger(__name__)


class JobicySearch:
    """Search jobs via Jobicy.com API (free, no auth).

    API endpoint: ``https://jobicy.com/api/v2/remote-jobs``
    Pagination: ``?count=N&tag=keyword`` (count caps results, tag filters).
    """

    BASE = "https://jobicy.com/api/v2/remote-jobs"
    SOURCE = "Jobicy"
    last_error = ""

    @staticmethod
    def search(query: str, limit: int = 50, max_pages: int = 3) -> List[Job]:
        JobicySearch.last_error = ""
        jobs = []
        try:
            # Jobicy filters by tag, so we pass the query as a tag.
            # The API returns up to ``count`` jobs per call.
            resp = requests.get(
                JobicySearch.BASE,
                params={"count": min(limit, 50), "tag": query.split()[0] if query else ""},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("jobs", [])[:limit]:
                salary = ""
                smin = item.get("salaryMin")
                smax = item.get("salaryMax")
                currency = item.get("salaryCurrency", "USD")
                period = item.get("salaryPeriod", "")
                if smin and smax:
                    salary = f"{currency} {smin:,}-{smax:,}/{period}"
                elif smin:
                    salary = f"{currency} {smin:,}+/{period}"

                jobs.append(Job(
                    title=item.get("jobTitle", ""),
                    company=item.get("companyName", ""),
                    location=item.get("jobGeo", ""),
                    url=item.get("url", ""),
                    description=(item.get("jobDescription") or "")[:500],
                    salary=salary,
                    source=JobicySearch.SOURCE,
                    remote=True,
                    country=item.get("country", ""),
                    timezone=item.get("timezone", ""),
                    tags=item.get("jobIndustry", []),
                    posted=item.get("pubDate", ""),
                ))

            return jobs
        except Exception as e:
            JobicySearch.last_error = str(e)
            logger.warning("Jobicy search failed: %s", e)
            return []
