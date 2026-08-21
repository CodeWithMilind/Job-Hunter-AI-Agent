"""Arbeitnow.com job source (free, no auth, paginated)."""

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from jobradar.models import Job

logger = logging.getLogger(__name__)

# ── Synonym / normalization maps for broader matching ──────────────────────

SYNONYM_MAP = {
    "engineer": ["developer", "programmer", "swe", "software engineer"],
    "developer": ["engineer", "programmer", "swe"],
    "devops": ["sre", "platform engineer", "infrastructure", "operations"],
    "frontend": ["front-end", "front end", "ui engineer"],
    "backend": ["back-end", "back end", "server-side"],
    "fullstack": ["full-stack", "full stack"],
    "ml": ["machine learning", "ai", "artificial intelligence", "data science"],
    "data": ["analytics", "bi", "business intelligence"],
}

# Common plurals / singulars to normalize
_PLURAL_SUFFIXES = [("ies", "y"), ("es", ""), ("s", "")]


def _normalize_word(w: str) -> str:
    """Strip plurals and lowercase."""
    w = w.lower().strip()
    for suffix, replacement in _PLURAL_SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            return w[: -len(suffix)] + replacement
    return w


def _build_searchable(query: str, title: str, desc: str, tags: List[str]) -> str:
    """Build a broad searchable string with synonyms for matching."""
    # Build searchable from job content ONLY (not the query)
    parts = [title.lower(), desc.lower()]
    parts.extend(t.lower() for t in tags)

    # Expand each query word with synonyms
    expanded = set()
    for word in query.lower().split():
        norm = _normalize_word(word)
        expanded.add(norm)
        expanded.add(word)
        for key, syns in SYNONYM_MAP.items():
            if norm == key or norm in syns:
                expanded.add(key)
                expanded.update(syns)

    searchable = " ".join(parts)

    # Check if ANY expanded term appears
    return searchable, expanded


def _matches_query(query: str, title: str, desc: str, tags: List[str]) -> bool:
    """Return True if the job is a reasonable match for the query.

    Uses synonym expansion and tag matching instead of naive substring.
    """
    searchable, expanded = _build_searchable(query, title, desc, tags)

    # If the full query phrase appears verbatim, it's a match
    if query.lower() in searchable:
        return True

    # Otherwise check if a majority of expanded terms appear
    hits = sum(1 for term in expanded if term in searchable)
    return hits >= max(1, len(expanded) // 2)


class ArbeitnowSearch:
    """Search jobs via Arbeitnow.com API (free, no auth, paginated).

    The API supports ``?page=N`` pagination (100 jobs per page).  We page
    through ``max_pages`` pages and apply client-side relevance filtering.
    """

    BASE = "https://www.arbeitnow.com/api/job-board-api"
    SOURCE = "Arbeitnow"
    last_error = ""

    @staticmethod
    def search(query: str, limit: int = 50, max_pages: int = 3) -> List[Job]:
        ArbeitnowSearch.last_error = ""
        jobs = []
        query_lower = query.lower()
        try:
            for page in range(1, max_pages + 1):
                resp = requests.get(
                    ArbeitnowSearch.BASE,
                    params={"page": page},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                page_data = data.get("data", [])
                if not page_data:
                    break

                for item in page_data:
                    title = item.get("title", "")
                    desc = BeautifulSoup(
                        item.get("description", "") or "", "html.parser"
                    ).get_text()[:500]
                    tags = item.get("tags", [])

                    if not _matches_query(query, title, desc, tags):
                        continue

                    salary = ""
                    if item.get("salary"):
                        salary = item["salary"]
                    jobs.append(Job(
                        title=title,
                        company=item.get("company_name", ""),
                        location=item.get("location", ""),
                        url=item.get("url", ""),
                        description=desc,
                        salary=salary,
                        source=ArbeitnowSearch.SOURCE,
                        remote=item.get("remote", False),
                        tags=tags,
                        posted=item.get("created_at", ""),
                    ))
                    if len(jobs) >= limit:
                        return jobs

                # Stop if no more pages
                next_url = data.get("links", {}).get("next")
                if not next_url:
                    break

            return jobs
        except Exception as e:
            ArbeitnowSearch.last_error = str(e)
            logger.warning("Arbeitnow search failed: %s", e)
            return []
