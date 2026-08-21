"""Job search sources — one module per API."""

from jobradar.sources.remotive import RemotiveSearch
from jobradar.sources.arbeitnow import ArbeitnowSearch
from jobradar.sources.linkedin import LinkedInSearch
from jobradar.sources.remoteok import RemoteOKSearch
from jobradar.sources.jobicy import JobicySearch
from jobradar.sources.himalayas import HimalayasSearch
from jobradar.sources.greenhouse import GreenhouseSearch
from jobradar.sources.ashby import AshbySearch
from jobradar.sources.base import JobSource, SourceFetchResult
from jobradar.sources.registry import SourceRegistry

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
