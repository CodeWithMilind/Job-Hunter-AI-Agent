"""Job search sources — one module per API."""

from hirevia.sources.remotive import RemotiveSearch
from hirevia.sources.arbeitnow import ArbeitnowSearch
from hirevia.sources.linkedin import LinkedInSearch
from hirevia.sources.remoteok import RemoteOKSearch
from hirevia.sources.jobicy import JobicySearch
from hirevia.sources.himalayas import HimalayasSearch
from hirevia.sources.greenhouse import GreenhouseSearch
from hirevia.sources.ashby import AshbySearch
from hirevia.sources.base import JobSource, SourceFetchResult
from hirevia.sources.registry import SourceRegistry

__all__ = [
    "RemotiveSearch",
    "ArbeitnowSearch",
    "LinkedInSearch",
    "RemoteOKSearch",
    "JobicySearch",
    "HimalayasSearch",
    "GreenhouseSearch",
    "AshbySearch",
    "JobSource",
    "SourceFetchResult",
    "SourceRegistry",
]
