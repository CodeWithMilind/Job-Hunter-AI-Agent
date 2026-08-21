"""Ashby ATS integration — fetches jobs from api.ashbyhq.com.

No auth required. Companies are configured in companies.yaml.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

import yaml
import requests

from hirevia.models import Job

logger = logging.getLogger(__name__)

_ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{company}"

_REMOTE_KEYWORDS = {"remote", "anywhere", "worldwide", "global", "distributed", "fully remote"}

_EMPLOYMENT_MAP = {
    "full_time": "Full-time",
    "full-time": "Full-time",
    "fulltime": "Full-time",
    "part_time": "Part-time",
    "part-time": "Part-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "internship": "Internship",
    "intern": "Internship",
    "temporary": "Temporary",
    "temp": "Temporary",
}


def _load_companies(path: str = "companies.yaml") -> List[str]:
    """Load Ashby company slugs from companies.yaml."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get("ashby", []) if isinstance(data, dict) else []
    except FileNotFoundError:
        logger.warning("companies.yaml not found — skipping Ashby")
        return []
    except Exception as e:
        logger.warning("Failed to load companies.yaml: %s", e)
        return []


def _parse_ashby_date(date_str: Optional[str]) -> str:
    """Normalize Ashby's ISO-ish dates to 'YYYY-MM-DD'.

    Ashby returns dates like: '2026-07-20T19:30:12.000Z'
    or '2026-07-20T19:30:12Z' or '2026-07-20'.
    """
    if not date_str:
        return ""
    try:
        # Handle various ISO formats
        clean = date_str.rstrip("Z").split("+")[0].split("T")[0]
        # Try full parse first
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(date_str))
        return m.group(1) if m else ""


def _detect_remote(job_data: dict) -> bool:
    """Detect remote status from Ashby job data.

    Ashby uses a locationConstraints field and sometimes
    includes remote info in the title or location text.
    """
    # Check location constraints
    constraints = job_data.get("locationConstraints", []) or []
    for c in constraints:
        if isinstance(c, dict):
            loc = (c.get("location", "") or "").lower()
            if any(kw in loc for kw in _REMOTE_KEYWORDS):
                return True

    # Check title
    title = (job_data.get("title", "") or "").lower()
    if any(kw in title for kw in _REMOTE_KEYWORDS):
        return True

    # Check employment type metadata
    meta = job_data.get("employmentCategory", "") or ""
    # No remote in employment category, but check description for clues

    return False


def _normalize_employment_type(raw: Optional[str]) -> str:
    """Normalize employment type strings to a consistent set."""
    if not raw:
        return ""
    key = raw.lower().strip().replace(" ", "_").replace("-", "_")
    return _EMPLOYMENT_MAP.get(key, raw.title())


def _normalize_job(job_data: dict, company: str) -> Optional[Job]:
    """Convert a raw Ashby job dict to a Job model."""
    title = (job_data.get("title", "") or "").strip()
    if not title:
        return None

    # Location from constraints
    location_parts = []
    constraints = job_data.get("locationConstraints", []) or []
    for c in constraints:
        if isinstance(c, dict):
            loc = c.get("location", "")
            if loc:
                location_parts.append(loc)
    location = ", ".join(location_parts) if location_parts else "Not specified"

    # URL
    job_id = job_data.get("id", "")
    url = job_data.get("applicationUrl", "")
    if not url and job_id:
        slug = company.lower()
        url = f"https://jobs.ashbyhq.com/{slug}"

    # Salary
    salary = ""
    compensation = job_data.get("compensation", {}) or {}
    if compensation:
        min_val = compensation.get("minAmount")
        max_val = compensation.get("maxAmount")
        currency = compensation.get("currency", "USD")
        if min_val and max_val:
            salary = f"{currency} {int(min_val):,}-{int(max_val):,}"
        elif min_val:
            salary = f"{currency} {int(min_val):,}+"

    # Tags from employment category + team
    tags = []
    emp_type = _normalize_employment_type(job_data.get("employmentCategory"))
    if emp_type:
        tags.append(emp_type)
    team = job_data.get("teamName", "")
    if team:
        tags.append(team)

    posted = _parse_ashby_date(job_data.get("publishedAt") or job_data.get("createdAt"))

    return Job(
        title=title,
        company=company.title(),
        location=location,
        url=url,
        description=(job_data.get("descriptionPlain", "") or job_data.get("descriptionHtml", ""))[:500],
        salary=salary,
        source="Ashby",
        remote=_detect_remote(job_data),
        location_restrictions=location_parts,
        country=job_data.get("country", ""),
        timezone=job_data.get("timezone", ""),
        tags=tags,
        posted=posted,
    )


class AshbySearch:
    """Search jobs across configured Ashby job boards."""

    SOURCE = "Ashby"
    last_error = ""

    @staticmethod
    def search(
        query: str,
        limit: int = 50,
        max_pages: int = 3,
        companies_path: str = "companies.yaml",
    ) -> List[Job]:
        """Search Ashby boards for the query.

        Ashby doesn't have a search endpoint — we fetch all jobs from
        each company board and filter client-side.
        """
        AshbySearch.last_error = ""
        companies = _load_companies(companies_path)
        if not companies:
            return []

        query_lower = query.lower().strip()
        query_words = set(query_lower.split())
        all_jobs: List[Job] = []

        for company in companies:
            try:
                url = _ASHBY_API.format(company=company)
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.debug("Ashby %s returned %d", company, resp.status_code)
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
                AshbySearch.last_error = str(e)
                logger.debug("Ashby %s search failed: %s", company, e)
                continue

        return all_jobs[:limit]
