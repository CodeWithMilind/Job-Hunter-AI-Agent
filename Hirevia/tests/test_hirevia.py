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

class TestRemotiveSource:
    @patch("hirevia.sources.remotive.requests.get")
    def test_search_basic(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "jobs": [
                    {
                        "title": "Python Dev",
                        "company_name": "Acme",
                        "candidate_required_location": "Anywhere",
                        "url": "https://example.com/1",
                        "description": "Build stuff",
                        "salary": "$100k",
                        "tags": ["python"],
                        "publication_date": "2024-01-01",
                    }
                ]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        from hirevia.sources.remotive import RemotiveSearch
        jobs = RemotiveSearch.search("python", limit=10)
        assert len(jobs) == 1
        assert jobs[0].title == "Python Dev"
        assert jobs[0].source == "Remotive"
        assert jobs[0].salary == "$100k"

    @patch("hirevia.sources.remotive.requests.get")
    def test_search_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        from hirevia.sources.remotive import RemotiveSearch
        jobs = RemotiveSearch.search("python", limit=10)
        assert jobs == []


class TestArbeitnowSource:
    @patch("hirevia.sources.arbeitnow.requests.get")
    def test_search_pagination(self, mock_get):
        # Page 1
        page1 = MagicMock(status_code=200)
        page1.json.return_value = {
            "data": [
                {"title": "Python Dev", "company_name": "Acme", "url": "a",
                 "description": "Python dev role", "tags": ["python"], "remote": True,
                 "location": "Berlin", "salary": "50k", "created_at": "2024-01-01"},
            ],
            "links": {"next": "https://example.com?page=2"},
        }
        page1.raise_for_status = MagicMock()
        # Page 2
        page2 = MagicMock(status_code=200)
        page2.json.return_value = {
            "data": [
                {"title": "Go Developer", "company_name": "Bob", "url": "b",
                 "description": "Go developer role", "tags": ["go"], "remote": False,
                 "location": "NYC", "salary": "", "created_at": "2024-01-02"},
            ],
            "links": {"next": None},
        }
        page2.raise_for_status = MagicMock()
        mock_get.side_effect = [page1, page2]

        from hirevia.sources.arbeitnow import ArbeitnowSearch
        jobs = ArbeitnowSearch.search("python", limit=50, max_pages=2)
        # Should have fetched 2 pages, but only python dev matches
        assert len(jobs) == 1
        assert jobs[0].title == "Python Dev"
        assert mock_get.call_count == 2


class TestRemoteOKSource:
    @patch("hirevia.sources.remoteok.requests.get")
    def test_search(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"last_updated": 1},  # metadata
                {
                    "position": "Python Engineer",
                    "company": "TechCo",
                    "location": "Remote",
                    "url": "https://remoteok.com/1",
                    "description": "Python role",
                    "tags": ["python", "django"],
                    "date": "2024-01-01",
                    "salary_min": 100000,
                    "salary_max": 150000,
                },
                {
                    "position": "Marketing Manager",
                    "company": "AdCo",
                    "location": "NYC",
                    "url": "https://remoteok.com/2",
                    "description": "Marketing",
                    "tags": ["marketing"],
                    "date": "2024-01-01",
                    "salary_min": 0,
                    "salary_max": 0,
                },
            ],
        )
        mock_get.return_value.raise_for_status = MagicMock()
        from hirevia.sources.remoteok import RemoteOKSearch
        jobs = RemoteOKSearch.search("python", limit=50)
        assert len(jobs) == 1
        assert jobs[0].title == "Python Engineer"
        assert "$100,000-$150,000" in jobs[0].salary


class TestJobicySource:
    @patch("hirevia.sources.jobicy.requests.get")
    def test_search(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "jobs": [
                    {
                        "id": 1,
                        "url": "https://jobicy.com/jobs/1",
                        "jobTitle": "React Developer",
                        "companyName": "StartupCo",
                        "jobGeo": "Remote",
                        "jobIndustry": ["Software"],
                        "jobDescription": "React work",
                        "pubDate": "2024-01-01",
                        "salaryMin": 80000,
                        "salaryMax": 120000,
                        "salaryCurrency": "USD",
                        "salaryPeriod": "year",
                    }
                ]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        from hirevia.sources.jobicy import JobicySearch
        jobs = JobicySearch.search("react", limit=50)
        assert len(jobs) == 1
        assert jobs[0].title == "React Developer"
        assert "USD" in jobs[0].salary


class TestHimalayasSource:
    @patch("hirevia.sources.himalayas.requests.get")
    def test_search(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "totalCount": 100,
                "offset": 0,
                "limit": 50,
                "jobs": [
                    {
                        "title": "DevOps Engineer",
                        "companyName": "CloudCo",
                        "locationRestrictions": ["United States"],
                        "minSalary": 90000,
                        "maxSalary": 130000,
                        "currency": "USD",
                        "salaryPeriod": "annual",
                        "description": "DevOps work",
                        "categories": ["DevOps"],
                        "pubDate": "2024-01-01",
                        "applicationLink": "https://apply.here/1",
                    }
                ]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        from hirevia.sources.himalayas import HimalayasSearch
        jobs = HimalayasSearch.search("devops", limit=50)
        assert len(jobs) == 1
        assert jobs[0].title == "DevOps Engineer"
        assert jobs[0].remote is True


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
