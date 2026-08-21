"""YAML-configured registry for built-in and future job sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml

from hirevia.models import Job
from hirevia.sources.base import JobSource
from hirevia.sources.ashby import AshbySearch
from hirevia.sources.arbeitnow import ArbeitnowSearch
from hirevia.sources.greenhouse import GreenhouseSearch
from hirevia.sources.himalayas import HimalayasSearch
from hirevia.sources.jobicy import JobicySearch
from hirevia.sources.linkedin import LinkedInSearch
from hirevia.sources.remotive import RemotiveSearch
from hirevia.sources.remoteok import RemoteOKSearch
from hirevia.sources.telegram import TelegramSearch


SOURCE_TYPES = {
    "API",
    "RSS",
    "Career Portal",
    "Company Career Page",
    "Telegram",
    "Custom",
}


@dataclass
class LegacySourceAdapter(JobSource):
    """Adapts existing source classes to the shared JobSource interface."""

    source_id: str
    name: str
    source_type: str
    enabled: bool
    metadata: Dict[str, Any]
    search: Callable[..., List[Job]]
    error_getter: Callable[[], str] = lambda: ""
    supports_location: bool = False
    needs_companies: bool = False

    def fetch(
        self,
        query: str,
        *,
        location: str = "",
        limit: int = 50,
        max_pages: int = 3,
        companies_path: str = "companies.yaml",
    ) -> List[Job]:
        kwargs: Dict[str, Any] = {
            "limit": limit,
            "max_pages": max_pages,
        }

        if self.supports_location:
            kwargs["location"] = location

        if self.needs_companies:
            kwargs["companies_path"] = companies_path

        jobs = self.search(query, **kwargs)

        if self.error_getter():
            raise RuntimeError(self.error_getter())

        return jobs


_BUILT_INS = {
    "remotive": (
        "Remotive",
        "API",
        RemotiveSearch,
        {},
    ),
    "arbeitnow": (
        "Arbeitnow",
        "API",
        ArbeitnowSearch,
        {},
    ),
    "remoteok": (
        "RemoteOK",
        "API",
        RemoteOKSearch,
        {},
    ),
    "jobicy": (
        "Jobicy",
        "API",
        JobicySearch,
        {},
    ),
    "himalayas": (
        "Himalayas",
        "API",
        HimalayasSearch,
        {},
    ),
    "greenhouse": (
        "Greenhouse",
        "Career Portal",
        GreenhouseSearch,
        {"provider": "Greenhouse"},
    ),
    "ashby": (
        "Ashby",
        "Career Portal",
        AshbySearch,
        {"provider": "Ashby"},
    ),
    "linkedin": (
        "LinkedIn",
        "Custom",
        LinkedInSearch,
        {"warning": "Opt-in public HTML source"},
    ),
}


class SourceRegistry:
    """Creates configured JobSource instances and exposes their metadata."""

    def __init__(self, config: Dict[str, Any] | None = None):
        self._config = (config or {}).get("sources", {})
        self._sources: Dict[str, JobSource] = {}
        self._register_built_ins()
        self._register_telegram()

    @classmethod
    def from_yaml(cls, path: str = "sources.yaml") -> "SourceRegistry":
        config_path = Path(path)

        if not config_path.exists():
            return cls()

        with config_path.open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file) or {}

        if not isinstance(loaded, dict):
            raise ValueError("sources.yaml must contain a mapping")

        return cls(loaded)

    def _register_built_ins(self) -> None:
        for (
            source_id,
            (name, source_type, source_class, default_metadata),
        ) in _BUILT_INS.items():

            config = self._config.get(source_id, {})

            if not isinstance(config, dict):
                config = {}

            metadata = {
                **default_metadata,
                **(config.get("metadata", {}) or {}),
            }

            self.register(
                LegacySourceAdapter(
                    source_id=source_id,
                    name=config.get("name", name),
                    source_type=config.get("type", source_type),
                    enabled=bool(
                        config.get(
                            "enabled",
                            source_id != "linkedin",
                        )
                    ),
                    metadata=metadata,
                    search=source_class.search,
                    error_getter=lambda cls=source_class: getattr(
                        cls,
                        "last_error",
                        "",
                    ),
                    supports_location=source_id == "linkedin",
                    needs_companies=source_id in {
                        "greenhouse",
                        "ashby",
                    },
                )
            )

    def _register_telegram(self) -> None:
        config = self._config.get("telegram", {})

        if not isinstance(config, dict):
            config = {}

        telegram = TelegramSearch()

        telegram.enabled = bool(
            config.get("enabled", True)
        )

        telegram.metadata = {
            **telegram.metadata,
            **(config.get("metadata", {}) or {}),
        }

        self.register(telegram)

    def register(self, source: JobSource) -> None:
        if source.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"Unsupported source type: {source.source_type}"
            )

        self._sources[source.source_id] = source

    def get(self, source_id: str) -> JobSource:
        return self._sources[source_id]

    def enabled_sources(
        self,
        overrides: Dict[str, bool] | None = None,
    ) -> List[JobSource]:

        overrides = overrides or {}

        return [
            source
            for source in self._sources.values()
            if overrides.get(
                source.source_id,
                source.enabled,
            )
        ]

    def metadata(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": source.source_id,
                "name": source.name,
                "type": source.source_type,
                "enabled": source.enabled,
                "metadata": source.metadata,
            }
            for source in self._sources.values()
        ]