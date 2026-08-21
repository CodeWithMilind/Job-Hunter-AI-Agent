"""LinkedIn public job search (web scraping, undocumented API).

.. warning::

    **LinkedIn ToS risk**: This source depends on undocumented HTML markup and
    may violate LinkedIn's Terms of Service.  It is **disabled by default**
    and must be explicitly enabled via ``--enable-linkedin`` or
    ``JOBRADAR_ENABLE_LINKEDIN=1``.  Use at your own risk.
"""

import logging
import os
from typing import List

import requests
from bs4 import BeautifulSoup

from jobradar.models import Job

logger = logging.getLogger(__name__)


def is_linkedin_enabled() -> bool:
    """Check if LinkedIn scraping is allowed by feature flag."""
    val = os.environ.get("JOBRADAR_ENABLE_LINKEDIN", "0")
    return val not in ("0", "false", "False", "no", "")


class LinkedInSearch:
    """Search jobs via LinkedIn's public job search (web scraping).

    WARNING: Depends on undocumented markup and may violate LinkedIn ToS.
    """

    SOURCE = "LinkedIn"
    last_error = ""

    @staticmethod
    def search(query: str, location: str = "", limit: int = 50, max_pages: int = 3) -> List[Job]:
        LinkedInSearch.last_error = ""
        if not is_linkedin_enabled():
            logger.info("LinkedIn source disabled (use --enable-linkedin to enable)")
            return []

        try:
            jobs = []
            for page_start in range(0, limit, 25):
                search_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                params = {
                    "keywords": query,
                    "location": location or "United States",
                    "start": page_start,
                    "sortBy": "DD",
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
                resp = requests.get(search_url, params=params, headers=headers, timeout=15)
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li")
                if not cards:
                    break

                for card in cards:
                    try:
                        # LinkedIn changed markup: the title/company links are now
                        # <a> tags (previously <h3>/<h4>). Support both, preferring
                        # the <a> variant so the parser keeps working across layouts.
                        title_el = card.find("a", class_="base-card__full-link") or card.find("h3", class_="base-card__full-link")
                        title = title_el.get_text(strip=True) if title_el else ""
                        url = title_el["href"].split("?")[0] if title_el and title_el.get("href") else ""
                        company_el = card.find("a", class_="hidden-nested-link") or card.find("h4", class_="hidden-nested-link")
                        company = company_el.get_text(strip=True) if company_el else ""
                        location_el = card.find("span", class_="job-search-card__location")
                        loc = location_el.get_text(strip=True) if location_el else ""
                        if not title:
                            continue
                        jobs.append(Job(
                            title=title,
                            company=company,
                            location=loc,
                            url=url,
                            source=LinkedInSearch.SOURCE,
                            remote="remote" in loc.lower(),
                        ))
                    except Exception:
                        continue

                if len(jobs) >= limit:
                    break

            return jobs[:limit]
        except Exception as e:
            LinkedInSearch.last_error = str(e)
            logger.warning("LinkedIn search failed: %s", e)
            return []
