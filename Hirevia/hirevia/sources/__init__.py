"""Job search sources — one module per API."""

from hirevia.sources.linkedin import LinkedInSearch
from hirevia.sources.greenhouse import GreenhouseSearch
from hirevia.sources.base import JobSource, SourceFetchResult
from hirevia.sources.registry import SourceRegistry

__all__ = [
    "LinkedInSearch",
    "GreenhouseSearch",
    "JobSource",
    "SourceFetchResult",
    "SourceRegistry",
]
