"""Tests for hirevia — Profile, Job dedup, parsing, sources, and more."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hirevia.models import Job, Profile
from hirevia.rating import AIRater
from hirevia.sources.linkedin import is_linkedin_enabled


# ──────────────────────────────────────────────────────────────────────────────
# Profile tests
# ──────────────────────────────────────────────────────────────────────────────

class TestProfile:
    def test_yaml_preferences_are_loaded(self, tmp_path):
        p = tmp_path / "profile.yaml"
        p.write_text("target_roles: [Data Analyst]\nkeywords: [Python]\nlocations: [India]\nexperience: [intern]\nexclude_keywords: [Senior]\nsources: [telegram]\ntelegram: {enabled: true}\nsettings: {scan_interval_seconds: 15}\n")
        profile = Profile.from_yaml(str(p))
        assert profile.target_roles == ["Data Analyst"]
        assert profile.keywords == ["Python"]
        assert profile.sources == ["telegram"]
        assert profile.settings["scan_interval_seconds"] == 15

    def test_from_yaml_valid(self, tmp_path):
        data = {
            "name": "Test User",
            "title": "Developer",
            "experience_years": 5,
            "skills": ["Python", "Go"],
            "desired_roles": ["Backend"],
            "salary_min": 100000,
            "salary_max": 200000,
            "location_preference": "Remote",
            "remote_ok": True,
            "industries": ["Tech"],
        }
        p = tmp_path / "profile.yaml"
        p.write_text(yaml.dump(data))
        profile = Profile.from_yaml(str(p))
        assert profile.name == "Test User"
        assert profile.title == "Developer"
        assert profile.experience_years == 5
        assert profile.skills == ["Python", "Go"]
        assert profile.desired_roles == ["Backend"]
        assert profile.salary_min == 100000
        assert profile.remote_ok is True

    def test_from_yaml_invalid_not_mapping(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("- just a list\n- item\n")
        with pytest.raises(ValueError, match="mapping"):
            Profile.from_yaml(str(p))

    def test_from_yaml_unknown_fields_warns(self, tmp_path):
        data = {"name": "X", "title": "Dev", "unknown_field": "oops"}
        p = tmp_path / "p.yaml"
        p.write_text(yaml.dump(data))
        with pytest.warns(UserWarning, match="unknown_field"):
            profile = Profile.from_yaml(str(p))
        assert profile.name == "X"

    def test_from_yaml_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Profile.from_yaml("/nonexistent/profile.yaml")

    def test_from_yaml_empty(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        profile = Profile.from_yaml(str(p))
        assert profile.name == "User"  # default


# ──────────────────────────────────────────────────────────────────────────────
# Job dedup tests
# ──────────────────────────────────────────────────────────────────────────────

class TestJobDedup:
    def test_dedup_basic(self):
        jobs = [
            Job(title="Python Dev", company="Acme", location="X", url="a"),
            Job(title="Python Dev", company="Acme", location="Y", url="b"),
            Job(title="Go Dev", company="Acme", location="X", url="c"),
        ]
        seen = set()
        unique = []
        for j in jobs:
            key = (j.title.lower().strip(), j.company.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(j)
        assert len(unique) == 2

    def test_dedup_case_insensitive(self):
        jobs = [
            Job(title="Python Dev", company="Acme", location="X", url="a"),
            Job(title="python dev", company="acme", location="Y", url="b"),
        ]
        seen = set()
        unique = []
        for j in jobs:
            key = (j.title.lower().strip(), j.company.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(j)
        assert len(unique) == 1

    def test_dedup_different_companies_same_title(self):
        jobs = [
            Job(title="Dev", company="Acme", location="X", url="a"),
            Job(title="Dev", company="Bob", location="X", url="b"),
        ]
        seen = set()
        unique = []
        for j in jobs:
            key = (j.title.lower().strip(), j.company.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(j)
        assert len(unique) == 2


# ──────────────────────────────────────────────────────────────────────────────
# _parse_response tests
# ──────────────────────────────────────────────────────────────────────────────

class TestParseResponse:
    def _make_rater(self):
        """Create an AIRater without connecting."""
        with patch.object(AIRater, "_detect_backend", return_value="llamacpp"):
            return AIRater(base_url="http://fake")

    def test_valid_json(self):
        rater = self._make_rater()
        job = Job(title="X", company="Y", location="", url="")
        content = '{"overall_score": 85, "rating": "Excellent", "skills_match": 90, "experience_fit": 80, "salary_fit": 70, "remote_fit": 95, "reasoning": "Great match"}'
        result = rater._parse_response(content, job, _retry=False)
        assert result.score == 85
        assert result.rating == "Excellent"
        assert result.skills_match == 90

    def test_fenced_json(self):
        rater = self._make_rater()
        job = Job(title="X", company="Y", location="", url="")
        content = '```json\n{"overall_score": 72, "rating": "Good", "skills_match": 80, "experience_fit": 70, "salary_fit": 60, "remote_fit": 80, "reasoning": "OK"}\n```'
        result = rater._parse_response(content, job, _retry=False)
        assert result.score == 72

    def test_malformed_returns_default(self):
        rater = self._make_rater()
        job = Job(title="X", company="Y", location="", url="")
        content = "This is not JSON at all, just random text with no score."
        result = rater._parse_response(content, job, _retry=False)
        # Should fall back to score=50 after all strategies fail
        assert result.score == 50
        assert result.rating == "Parse Error"

    def test_prose_score_extraction(self):
        rater = self._make_rater()
        job = Job(title="X", company="Y", location="", url="")
        content = "Overall I'd give this job a score of 78/100 because it matches well."
        result = rater._parse_response(content, job, _retry=False)
        assert result.score == 78

    def test_retry_on_malformed(self):
        rater = self._make_rater()
        job = Job(title="X", company="Y", location="", url="")
        content = "garbage"
        # _parse_response should be called twice (initial + retry)
        with patch.object(rater, '_parse_response', wraps=rater._parse_response) as mock:
            result = rater._parse_response(content, job, _retry=True)
            # The recursive retry call should happen
            assert mock.call_count == 2


# ──────────────────────────────────────────────────────────────────────────────
# LinkedIn feature flag tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLinkedInFeatureFlag:
    def test_disabled_by_default(self):
        env = os.environ.copy()
        env.pop("hirevia_ENABLE_LINKEDIN", None)
        with patch.dict(os.environ, env, clear=True):
            assert is_linkedin_enabled() is False

    def test_enabled_with_1(self):
        with patch.dict(os.environ, {"hirevia_ENABLE_LINKEDIN": "1"}):
            assert is_linkedin_enabled() is True

    def test_disabled_with_0(self):
        with patch.dict(os.environ, {"hirevia_ENABLE_LINKEDIN": "0"}):
            assert is_linkedin_enabled() is False


# ──────────────────────────────────────────────────────────────────────────────
# Source tests with mocked HTTP
# ──────────────────────────────────────────────────────────────────────────────

class TestLinkedInSource:
    @patch("hirevia.sources.linkedin.requests.get")
    def test_disabled_by_default(self, mock_get):
        from hirevia.sources.linkedin import LinkedInSearch
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("hirevia_ENABLE_LINKEDIN", None)
            jobs = LinkedInSearch.search("python")
            assert jobs == []
            mock_get.assert_not_called()

    @patch("hirevia.sources.linkedin.requests.get")
    def test_enabled_returns_jobs(self, mock_get):
        from hirevia.sources.linkedin import LinkedInSearch
        with patch.dict(os.environ, {"hirevia_ENABLE_LINKEDIN": "1"}):
            mock_resp = MagicMock(status_code=200, text="""
                <ul>
                    <li>
                        <h3 class="base-card__full-link" href="https://linkedin.com/jobs/1?trk=1">Python Dev</h3>
                        <h4 class="hidden-nested-link">Acme Corp</h4>
                        <span class="job-search-card__location">Remote</span>
                    </li>
                </ul>
            """)
            mock_get.return_value = mock_resp
            jobs = LinkedInSearch.search("python", limit=10)
            assert len(jobs) == 1
            assert jobs[0].title == "Python Dev"
            assert jobs[0].source == "LinkedIn"

    @patch("hirevia.sources.linkedin.requests.get")
    def test_enabled_new_markup_returns_jobs(self, mock_get):
        """LinkedIn moved title/company to <a> tags (2026 markup change)."""
        from hirevia.sources.linkedin import LinkedInSearch
        with patch.dict(os.environ, {"hirevia_ENABLE_LINKEDIN": "1"}):
            mock_resp = MagicMock(status_code=200, text="""
                <ul>
                    <li>
                        <a class="base-card__full-link absolute top-0" href="https://in.linkedin.com/jobs/view/123?trk=public_jobs_js">Rust Engineer</a>
                        <a class="hidden-nested-link" href="https://www.linkedin.com/company/acme">Acme Corp</a>
                        <span class="job-search-card__location">London, England, United Kingdom</span>
                    </li>
                </ul>
            """)
            mock_get.return_value = mock_resp
            jobs = LinkedInSearch.search("rust", limit=10)
            assert len(jobs) == 1
            assert jobs[0].title == "Rust Engineer"
            assert jobs[0].company == "Acme Corp"
            assert jobs[0].url == "https://in.linkedin.com/jobs/view/123"
            assert jobs[0].source == "LinkedIn"


# ──────────────────────────────────────────────────────────────────────────────
# Interactive mode regression tests
# ──────────────────────────────────────────────────────────────────────────────

class TestUnifiedPipeline:
    def test_pipeline_uses_shared_registry_and_dedup(self, monkeypatch):
        from hirevia.pipeline import search_jobs

        class FakeSource:
            def __init__(self, name):
                self.name = name
                self.source_id = name.lower().replace(" ", "_")
                self.source_type = "API"
                self.enabled = True

            def fetch_safely(self, query, **kwargs):
                return MagicMock(
                    jobs=[
                        Job(title="Python Developer Intern", company="Acme", location="India", url="https://example.com/a", source="", source_type="API", description="Python internship"),
                        Job(title="Python Developer Intern", company="Acme", location="India", url="https://example.com/b", source="", source_type="API", description="Python internship"),
                        Job(title="Go Developer Intern", company="Acme", location="India", url="https://example.com/c", source="", source_type="API", description="Go internship"),
                    ],
                    error="",
                )

        class FakeRegistry:
            @staticmethod
            def from_yaml(path):
                return FakeRegistry()

            def enabled_sources(self, overrides=None):
                return [FakeSource("Alpha"), FakeSource("Beta")]

        class FakeCache:
            def __init__(self, ttl_days=7):
                pass

            def filter_new(self, jobs):
                return jobs

            def mark_seen(self, jobs):
                pass

            def close(self):
                pass

        class FakeRater:
            available = True

            def __init__(self, *args, **kwargs):
                pass

            def rate_jobs(self, jobs, profile, on_progress=None):
                for job in jobs:
                    job.score = 80
                    job.rating = "Good"

        monkeypatch.setattr("hirevia.pipeline.SourceRegistry", FakeRegistry)
        monkeypatch.setattr("hirevia.pipeline.SeenJobsCache", FakeCache)
        monkeypatch.setattr("hirevia.pipeline.AIRater", FakeRater)

        jobs = search_jobs(query="python", profile=Profile(target_roles=["Python Developer"], locations=["India"], experience=["intern"]), ai_enabled=True, no_cache=True)
        assert len(jobs) == 1
        titles = {job.title for job in jobs}
        assert titles == {"Python Developer Intern"}
        assert all(job.score >= 0 for job in jobs)

    def test_pipeline_reports_source_errors_and_scan_counts(self, monkeypatch):
        from hirevia.pipeline import search_jobs

        class Source:
            source_id = "broken"
            name = "Broken"
            source_type = "API"
            enabled = True

            def fetch_safely(self, query, **kwargs):
                from hirevia.sources.base import SourceFetchResult
                return SourceFetchResult(source_id=self.source_id, error="upstream unavailable")

        class Registry:
            @staticmethod
            def from_yaml(path): return Registry()
            def enabled_sources(self, overrides=None): return [Source()]

        monkeypatch.setattr("hirevia.pipeline.SourceRegistry", Registry)
        stats = {}
        assert search_jobs("Python", ai_enabled=False, no_cache=True, show_output=False, scan_stats=stats) == []
        assert stats["raw_jobs"] == 0
        assert stats["sources"]["broken"]["error"] == "upstream unavailable"


class TestInteractiveMode:
    def test_interactive_quit_no_crash(self, monkeypatch, capsys):
        """Regression: 'python -m hirevia' crashed with NameError: LLM_MODEL
        not defined when a search was run from interactive mode."""
        from hirevia.cli import interactive_mode

        # Feed: a query, empty location, then quit
        inputs = iter(["python dev", "", "quit"])
        monkeypatch.setattr("hirevia.cli.console.input", lambda prompt="": next(inputs))

        called = {}

        def fake_search_jobs(**kwargs):
            called["kwargs"] = kwargs
            return []

        monkeypatch.setattr("hirevia.cli.search_jobs", fake_search_jobs)

        interactive_mode(profile=None)  # must not raise

        out = capsys.readouterr().out
        assert "Goodbye" in out
        assert "llm_model" in called["kwargs"]
        # Must pass a model name (either auto-detected or the default fallback)
        assert called["kwargs"]["llm_model"]
