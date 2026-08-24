from dataclasses import dataclass
from unittest.mock import patch

from hirevia.models import Job
from hirevia.sources.base import JobSource
from hirevia.sources.registry import SourceRegistry


def test_built_in_sources_are_registered_with_categories():
    registry = SourceRegistry({"sources": {}})
    assert registry.get("greenhouse").source_type == "Career Portal"
    assert registry.get("linkedin").enabled is False
    assert {source["id"] for source in registry.metadata()} == {"greenhouse", "linkedin"}
    assert "telegram" not in {source.source_id for source in registry.enabled_sources()}


def test_yaml_config_enables_and_disables_sources(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text("sources:\n  linkedin: { enabled: true }\n")
    registry = SourceRegistry.from_yaml(str(config))
    enabled = {source.source_id for source in registry.enabled_sources()}
    assert "linkedin" in enabled


@dataclass
class _TestSource(JobSource):
    source_id: str = "test"
    name: str = "Test Source"
    source_type: str = "Custom"
    enabled: bool = True
    metadata: dict = None
    fails: bool = False

    def __post_init__(self):
        self.metadata = self.metadata or {"kind": "test"}

    def fetch(self, query, **kwargs):
        if self.fails:
            raise RuntimeError("source unavailable")
        return [Job(title="Engineer", company="Acme", location="Anywhere", url="https://example.test/job")]


def test_source_failure_is_isolated():
    failed = _TestSource(fails=True).fetch_safely("python")
    working = _TestSource().fetch_safely("python")
    assert failed.jobs == []
    assert "source unavailable" in failed.error
    assert len(working.jobs) == 1


def test_source_metadata_is_attached_to_collected_jobs():
    result = _TestSource().fetch_safely("python")
    job = result.jobs[0]
    assert job.source == "Test Source"
    assert job.source_type == "Custom"
    assert job.original_url == "https://example.test/job"
    assert job.source_metadata == {"kind": "test"}


def test_source_last_error_is_exposed_by_safe_fetch():
    source = _TestSource()
    source.last_error = "temporary outage"
    result = source.fetch_safely("python")
    assert result.jobs == []
    assert result.error == "temporary outage"


def test_custom_source_registration_is_supported():
    registry = SourceRegistry({"sources": {}})
    registry.register(_TestSource(source_id="custom_feed", name="Custom Feed"))
    assert registry.get("custom_feed").name == "Custom Feed"
