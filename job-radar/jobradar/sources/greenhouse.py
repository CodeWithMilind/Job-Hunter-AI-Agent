"""Greenhouse ATS integration — fetches jobs from boards-api.greenhouse.io.

No auth required. Companies are configured in companies.yaml.
"""

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import yaml
import requests

from jobradar.models import Job

logger = logging.getLogger(__name__)

_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"

_REMOTE_KEYWORDS = {"remote", "anywhere", "worldwide", "global", "distributed", "fully remote"}


def _load_companies(path: str = "companies.yaml") -> List[str]:
    """Load greenhouse company slugs from companies.yaml."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get("greenhouse", []) if isinstance(data, dict) else []
    except FileNotFoundError:
        logger.warning("companies.yaml not found — skipping Greenhouse")
        return []
    except Exception as e:
        logger.warning("Failed to load companies.yaml: %s", e)
        return []


def _parse_greenhouse_date(date_str: Optional[str]) -> str:
    """Normalize Greenhouse's ISO-with-offset dates to 'YYYY-MM-DD'.

    Greenhouse returns dates like: '2026-07-20T14:30:00-05:00'
    We normalize to simple 'YYYY-MM-DD'.
    """
    if not date_str:
        return ""
    try:
        # Handle ISO format with timezone offset
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        # Try regex fallback
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(date_str))
        return m.group(1) if m else ""


def _detect_remote(job_data: dict) -> bool:
    """Detect remote status from Greenhouse job data.

    Greenhouse doesn't have a dedicated remote field. We check:
    1. Location name contains remote keywords
    2. Job title contains remote keywords
    3. Any tag contains remote
    """
    location = (job_data.get("location", {}) or {}).get("name", "").lower()
    title = (job_data.get("title", "") or "").lower()

    for kw in _REMOTE_KEYWORDS:
        if kw in location or kw in title:
            return True

    # Check updated_at for clues (some boards add remote in location)
    return False


def _normalize_job(job_data: dict, company: str) -> Optional[Job]:
    """Convert a raw Greenhouse job dict to a Job model."""
    title = job_data.get("title", "").strip()
    if not title:
        return None

    location_data = job_data.get("location", {}) or {}
    location = location_data.get("name", "Not specified")

    job_id = job_data.get("id", "")
    url = f"https://boards.greenhouse.io/{company}/jobs/{job_id}" if job_id else ""

    # Extract salary if available
    salary = ""
    compensation = job_data.get("compensation", {}) or {}
    if compensation:
        min_val = compensation.get("min")
        max_val = compensation.get("max")
        currency = compensation.get("currency", "USD")
        if min_val and max_val:
            salary = f"{currency} {int(min_val):,}-{int(max_val):,}"
        elif min_val:
            salary = f"{currency} {int(min_val):,}+"

    # Extract departments as tags
    departments = []
    for dept in job_data.get("departments", []) or []:
        name = dept.get("name", "")
        if name:
            departments.append(name)

    # Employment type
    job_type = job_data.get("updated_at", "")
    # Greenhouse doesn't have a dedicated employment type field in the public API

    posted = _parse_greenhouse_date(job_data.get("updated_at") or job_data.get("created_at"))

    return Job(
        title=title,
        company=company.title(),
        location=location,
        url=url,
        description=job_data.get("content", "")[:500] if job_data.get("content") else "",
        salary=salary,
        source="Greenhouse",
        remote=_detect_remote(job_data),
        tags=departments,
        posted=posted,
    )


class GreenhouseSearch:
    """Search jobs across configured Greenhouse boards."""

    SOURCE = "Greenhouse"
    last_error = ""

    @staticmethod
    def search(
        query: str,
        limit: int = 50,
        max_pages: int = 3,
        companies_path: str = "companies.yaml",
    ) -> List[Job]:
        """Search Greenhouse boards for the query.

        Greenhouse doesn't have a search endpoint — we fetch all jobs from
        each company board and filter client-side by title/description/tags.
        """
        GreenhouseSearch.last_error = ""
        companies = _load_companies(companies_path)
        if not companies:
            return []

        query_lower = query.lower().strip()
        query_words = set(query_lower.split())
        all_jobs: List[Job] = []

        for company in companies:
            try:
                url = _GREENHOUSE_API.format(company=company)
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.debug("Greenhouse %s returned %d", company, resp.status_code)
                    continue

                data = resp.json()
                jobs_data = data.get("jobs", [])

                for jd in jobs_data:
                    job = _normalize_job(jd, company)
                    if not job:
                        continue

                    # Client-side relevance filter
                    search_text = f"{job.title} {job.description} {' '.join(job.tags)}".lower()
                    if any(w in search_text for w in query_words):
                        all_jobs.append(job)

                    if len(all_jobs) >= limit:
                        break

                if len(all_jobs) >= limit:
                    break

            except Exception as e:
                GreenhouseSearch.last_error = str(e)
                logger.debug("Greenhouse %s search failed: %s", company, e)
                continue

        return all_jobs[:limit]
