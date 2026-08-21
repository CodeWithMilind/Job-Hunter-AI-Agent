"""Himalayas.app job source (free, no auth, paginated)."""

import logging
from typing import List

import requests

from jobradar.models import Job

logger = logging.getLogger(__name__)


class HimalayasSearch:
    """Search jobs via Himalayas.app API (free, no auth).

    API endpoint: ``https://himalayas.app/jobs/api``
    Pagination: ``?limit=N&offset=N`` with server-side search.
    """

    BASE = "https://himalayas.app/jobs/api"
    SOURCE = "Himalayas"
    last_error = ""

    @staticmethod
    def search(query: str, limit: int = 50, max_pages: int = 3) -> List[Job]:
        HimalayasSearch.last_error = ""
        jobs = []
        try:
            per_page = min(limit, 50)
            for page in range(max_pages):
                offset = page * per_page
                resp = requests.get(
                    HimalayasSearch.BASE,
                    params={
                        "limit": per_page,
                        "offset": offset,
                        "search": query,
                    },
                    headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                page_jobs = data.get("jobs", [])
                if not page_jobs:
                    break

                for item in page_jobs:
                    salary = ""
                    smin = item.get("minSalary")
                    smax = item.get("maxSalary")
                    currency = item.get("currency", "USD")
                    period = item.get("salaryPeriod", "")
                    if smin and smax:
                        salary = f"{currency} {smin:,}-{smax:,}/{period}"
                    elif smin:
                        salary = f"{currency} {smin:,}+/{period}"

                    locs = item.get("locationRestrictions", [])
                    location = ", ".join(locs[:2]) if locs else ""

                    # Derive URL from guid or applicationLink
                    url = item.get("applicationLink") or item.get("guid") or ""

                    jobs.append(Job(
                        title=item.get("title", ""),
                        company=item.get("companyName", ""),
                        location=location,
                        url=url,
                        description=(item.get("description") or item.get("excerpt") or "")[:500],
                        salary=salary,
                        source=HimalayasSearch.SOURCE,
                        remote=True,  # Himalayas is a remote job board
                        location_restrictions=locs,
                        country=item.get("country", ""),
                        timezone=item.get("timezone", ""),
                        tags=item.get("categories", []),
                        posted=item.get("pubDate", ""),
                    ))
                    if len(jobs) >= limit:
                        return jobs

                # If we got fewer than per_page, no more pages
                if len(page_jobs) < per_page:
                    break

            return jobs
        except Exception as e:
            HimalayasSearch.last_error = str(e)
            logger.warning("Himalayas search failed: %s", e)
            return []
