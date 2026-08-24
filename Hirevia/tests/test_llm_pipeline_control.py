"""Prove semantic LLM decisions control final pipeline results."""

from unittest.mock import MagicMock
from types import SimpleNamespace

from hirevia.models import Job, Profile
from hirevia.llm_relevance import LLMJobResult, SearchIntent


def test_llm_relevance_removes_candidate(monkeypatch):
    import hirevia.pipeline as pipeline

    good = Job("Python Developer Intern", "GoodCo", "India", "https://good.test/job", "Python backend internship")
    weak = Job("Backend Engineer Intern", "WeakCo", "India", "https://weak.test/job", "Python backend internship")

    class Source:
        name = "Test"
        source_id = "test"
        source_type = "API"
        enabled = True

        def fetch_safely(self, query, **kwargs):
            return MagicMock(jobs=[good, weak], error="")

    class Registry:
        @staticmethod
        def from_yaml(path):
            return Registry()

        def enabled_sources(self, overrides=None):
            return [Source()]

    class Cache:
        def __init__(self, **kwargs): pass
        def filter_new(self, jobs): return jobs
        def mark_seen(self, jobs): pass
        def close(self): pass

    class Rater:
        available = True
        base_url = "http://local"
        def __init__(self, *args, **kwargs): pass
        def rate_jobs(self, jobs, profile, on_progress=None):
            for job in jobs: job.score = 80

    class Semantic:
        def __init__(self, *args, **kwargs): self.calls = 0
        def understand_query(self, query, location):
            return SearchIntent(query, target_roles=[query])
        def evaluate_jobs(self, query, intent, jobs, limit=30):
            self.calls += 1
            return {
                self.job_key(query, good)[1]: LLMJobResult(True, 95, "Python required"),
                self.job_key(query, weak)[1]: LLMJobResult(False, 8, "Weak role match"),
            }
        @staticmethod
        def job_key(query, job):
            return (query, job.url)

    semantic = Semantic()
    monkeypatch.setattr(pipeline, "SourceRegistry", Registry)
    monkeypatch.setattr(pipeline, "SeenJobsCache", Cache)
    monkeypatch.setattr(pipeline, "AIRater", Rater)
    monkeypatch.setattr(pipeline, "NVIDIAClient", lambda: SimpleNamespace(available=False))
    monkeypatch.setattr(pipeline, "LLMRelevance", lambda *args, **kwargs: semantic)
    monkeypatch.setattr(pipeline, "verify_application_link", lambda *args, **kwargs: pipeline.LinkState.UNKNOWN)

    jobs = pipeline.search_jobs(
        "Python Developer",
        profile=Profile(target_roles=["Python Developer"], locations=["India"], experience=["intern"]),
        ai_enabled=True,
        no_cache=True,
        show_output=False,
    )

    assert semantic.calls == 1
    assert [job.company for job in jobs] == ["GoodCo"]
    assert jobs[0].source_metadata["llm_relevance"] == 95
