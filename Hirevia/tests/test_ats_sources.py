"""Tests for Greenhouse and Ashby ATS sources + normalization + cache."""

import json
import os
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest
import yaml

from hirevia.models import Job, Profile


# ─── Greenhouse normalization ──────────────────────────────────────────────

class TestGreenhouseDateNormalization:
    def test_iso_with_offset(self):
        from hirevia.sources.greenhouse import _parse_greenhouse_date
        assert _parse_greenhouse_date("2026-07-20T14:30:00-05:00") == "2026-07-20"

    def test_iso_utc(self):
        from hirevia.sources.greenhouse import _parse_greenhouse_date
        assert _parse_greenhouse_date("2026-01-01T00:00:00Z") == "2026-01-01"

    def test_date_only(self):
        from hirevia.sources.greenhouse import _parse_greenhouse_date
        assert _parse_greenhouse_date("2026-07-20") == "2026-07-20"

    def test_empty(self):
        from hirevia.sources.greenhouse import _parse_greenhouse_date
        assert _parse_greenhouse_date(None) == ""
        assert _parse_greenhouse_date("") == ""

    def test_garbage(self):
        from hirevia.sources.greenhouse import _parse_greenhouse_date
        assert _parse_greenhouse_date("not a date") == ""


class TestGreenhouseRemoteDetection:
    def test_remote_in_location(self):
        from hirevia.sources.greenhouse import _detect_remote
        assert _detect_remote({"location": {"name": "Remote - US"}}) is True

    def test_remote_in_title(self):
        from hirevia.sources.greenhouse import _detect_remote
        assert _detect_remote({"title": "Remote Python Developer"}) is True

    def test_not_remote(self):
        from hirevia.sources.greenhouse import _detect_remote
        assert _detect_remote({"location": {"name": "San Francisco, CA"}, "title": "Software Engineer"}) is False

    def test_empty(self):
        from hirevia.sources.greenhouse import _detect_remote
        assert _detect_remote({}) is False


class TestGreenhouseJobNormalization:
    def test_basic_job(self):
        from hirevia.sources.greenhouse import _normalize_job
        data = {
            "id": 12345,
            "title": "Python Developer",
            "location": {"name": "Remote"},
            "departments": [{"name": "Engineering"}],
            "updated_at": "2026-07-20T14:30:00-05:00",
            "content": "<p>Build things</p>",
        }
        job = _normalize_job(data, "gitlab")
        assert job.title == "Python Developer"
        assert job.company == "Gitlab"
        assert job.location == "Remote"
        assert job.remote is True
        assert job.source == "Greenhouse"
        assert "Engineering" in job.tags
        assert job.posted == "2026-07-20"
        assert "12345" in job.url

    def test_empty_title_skipped(self):
        from hirevia.sources.greenhouse import _normalize_job
        assert _normalize_job({"title": ""}, "test") is None
        assert _normalize_job({}, "test") is None

    def test_salary_extraction(self):
        from hirevia.sources.greenhouse import _normalize_job
        data = {
            "id": 1,
            "title": "Eng",
            "location": {"name": "NYC"},
            "compensation": {"min": 100000, "max": 150000, "currency": "USD"},
        }
        job = _normalize_job(data, "acme")
        assert "100,000" in job.salary
        assert "150,000" in job.salary


class TestGreenhouseSearch:
    @patch("hirevia.sources.greenhouse.requests.get")
    def test_search_filters_by_query(self, mock_get, tmp_path):
        companies_file = tmp_path / "companies.yaml"
        companies_file.write_text(yaml.dump({"greenhouse": ["testcorp"]}))

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"jobs": [
                {"id": 1, "title": "Python Developer", "location": {"name": "Remote"},
                 "departments": [], "updated_at": "2026-07-01"},
                {"id": 2, "title": "Marketing Manager", "location": {"name": "NYC"},
                 "departments": [], "updated_at": "2026-07-01"},
                {"id": 3, "title": "Senior Python Engineer", "location": {"name": "Remote"},
                 "departments": [], "updated_at": "2026-07-01"},
            ]}
        )
        from hirevia.sources.greenhouse import GreenhouseSearch
        jobs = GreenhouseSearch.search("python", companies_path=str(companies_file))
        assert len(jobs) == 2
        assert all("python" in j.title.lower() for j in jobs)

    @patch("hirevia.sources.greenhouse.requests.get")
    def test_search_handles_error(self, mock_get, tmp_path):
        companies_file = tmp_path / "companies.yaml"
        companies_file.write_text(yaml.dump({"greenhouse": ["testcorp"]}))
        mock_get.side_effect = Exception("timeout")
        from hirevia.sources.greenhouse import GreenhouseSearch
        jobs = GreenhouseSearch.search("python", companies_path=str(companies_file))
        assert jobs == []

    def test_missing_companies_file(self):
        from hirevia.sources.greenhouse import GreenhouseSearch
        jobs = GreenhouseSearch.search("python", companies_path="/nonexistent.yaml")
        assert jobs == []


# ─── Ashby normalization ──────────────────────────────────────────────────

class TestAshbyDateNormalization:
    def test_iso_with_millis(self):
        from hirevia.sources.ashby import _parse_ashby_date
        assert _parse_ashby_date("2026-07-20T19:30:12.000Z") == "2026-07-20"

    def test_iso_utc(self):
        from hirevia.sources.ashby import _parse_ashby_date
        assert _parse_ashby_date("2026-07-20T19:30:12Z") == "2026-07-20"

    def test_date_only(self):
        from hirevia.sources.ashby import _parse_ashby_date
        assert _parse_ashby_date("2026-07-20") == "2026-07-20"

    def test_empty(self):
        from hirevia.sources.ashby import _parse_ashby_date
        assert _parse_ashby_date(None) == ""
        assert _parse_ashby_date("") == ""


class TestAshbyEmploymentNormalization:
    def test_full_time_variants(self):
        from hirevia.sources.ashby import _normalize_employment_type
        assert _normalize_employment_type("full_time") == "Full-time"
        assert _normalize_employment_type("full-time") == "Full-time"
        assert _normalize_employment_type("fulltime") == "Full-time"

    def test_part_time(self):
        from hirevia.sources.ashby import _normalize_employment_type
        assert _normalize_employment_type("part_time") == "Part-time"

    def test_contract(self):
        from hirevia.sources.ashby import _normalize_employment_type
        assert _normalize_employment_type("contract") == "Contract"

    def test_empty(self):
        from hirevia.sources.ashby import _normalize_employment_type
        assert _normalize_employment_type(None) == ""
        assert _normalize_employment_type("") == ""

    def test_unknown_passthrough(self):
        from hirevia.sources.ashby import _normalize_employment_type
        assert _normalize_employment_type("freelance") == "Freelance"


class TestAshbyRemoteDetection:
    def test_remote_in_constraints(self):
        from hirevia.sources.ashby import _detect_remote
        assert _detect_remote({"locationConstraints": [{"location": "Remote"}]}) is True

    def test_remote_in_title(self):
        from hirevia.sources.ashby import _detect_remote
        assert _detect_remote({"title": "Remote Engineer"}) is True

    def test_not_remote(self):
        from hirevia.sources.ashby import _detect_remote
        assert _detect_remote({"title": "Engineer", "locationConstraints": [{"location": "NYC"}]}) is False


class TestAshbyJobNormalization:
    def test_basic_job(self):
        from hirevia.sources.ashby import _normalize_job
        data = {
            "id": "abc-123",
            "title": "AI Engineer",
            "locationConstraints": [{"location": "Remote"}],
            "employmentCategory": "full_time",
            "teamName": "Research",
            "publishedAt": "2026-07-20T19:30:12.000Z",
            "descriptionPlain": "Build AI models",
            "applicationUrl": "https://jobs.ashbyhq.com/openai/apply/123",
        }
        job = _normalize_job(data, "openai")
        assert job.title == "AI Engineer"
        assert job.company == "Openai"
        assert job.location == "Remote"
        assert job.remote is True
        assert job.source == "Ashby"
        assert "Full-time" in job.tags
        assert "Research" in job.tags
        assert job.posted == "2026-07-20"
        assert job.url == "https://jobs.ashbyhq.com/openai/apply/123"

    def test_empty_title_skipped(self):
        from hirevia.sources.ashby import _normalize_job
        assert _normalize_job({"title": ""}, "test") is None

    def test_salary_extraction(self):
        from hirevia.sources.ashby import _normalize_job
        data = {
            "id": "1",
            "title": "Eng",
            "locationConstraints": [],
            "compensation": {"minAmount": 120000, "maxAmount": 180000, "currency": "USD"},
        }
        job = _normalize_job(data, "acme")
        assert "120,000" in job.salary
        assert "180,000" in job.salary


class TestAshbySearch:
    @patch("hirevia.sources.ashby.requests.get")
    def test_search_filters_by_query(self, mock_get, tmp_path):
        companies_file = tmp_path / "companies.yaml"
        companies_file.write_text(yaml.dump({"ashby": ["testcorp"]}))

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"jobs": [
                {"id": "1", "title": "Python Developer", "locationConstraints": []},
                {"id": "2", "title": "Marketing Manager", "locationConstraints": []},
            ]}
        )
        from hirevia.sources.ashby import AshbySearch
        jobs = AshbySearch.search("python", companies_path=str(companies_file))
        assert len(jobs) == 1
        assert jobs[0].title == "Python Developer"

    @patch("hirevia.sources.ashby.requests.get")
    def test_handles_non_200(self, mock_get, tmp_path):
        companies_file = tmp_path / "companies.yaml"
        companies_file.write_text(yaml.dump({"ashby": ["testcorp"]}))
        mock_get.return_value = MagicMock(status_code=404)
        from hirevia.sources.ashby import AshbySearch
        jobs = AshbySearch.search("python", companies_path=str(companies_file))
        assert jobs == []


# ─── Seen-jobs cache ──────────────────────────────────────────────────────

class TestSeenJobsCache:
    def test_basic_cache_flow(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        db_path = str(tmp_path / "test.db")
        cache = SeenJobsCache(db_path=db_path, ttl_days=7)

        jobs = [
            Job(title="Python Dev", company="Acme", location="X", url="u1", source="Remotive"),
            Job(title="Go Dev", company="Beta", location="Y", url="u2", source="Remotive"),
        ]

        # First run — all new
        new = cache.filter_new(jobs)
        assert len(new) == 2

        # Mark as seen
        cache.mark_seen(jobs)

        # Second run — all cached
        new = cache.filter_new(jobs)
        assert len(new) == 0

        cache.close()

    def test_partial_overlap(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        cache = SeenJobsCache(db_path=str(tmp_path / "test.db"), ttl_days=7)

        jobs_a = [
            Job(title="Python Dev", company="Acme", location="X", url="u1", source="Remotive"),
            Job(title="Go Dev", company="Beta", location="Y", url="u2", source="Remotive"),
        ]
        cache.mark_seen(jobs_a)

        jobs_b = [
            Job(title="Python Dev", company="Acme", location="X", url="u1", source="Remotive"),  # seen
            Job(title="Rust Dev", company="Gamma", location="Z", url="u3", source="Remotive"),  # new
        ]
        new = cache.filter_new(jobs_b)
        assert len(new) == 1
        assert new[0].title == "Rust Dev"
        cache.close()

    def test_ttl_expiry(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        cache = SeenJobsCache(db_path=str(tmp_path / "test.db"), ttl_days=1)

        job = Job(title="X", company="Y", location="Z", url="u", source="S")
        cache.mark_seen([job])

        # Manually set seen_at to 2 days ago
        cache.conn.execute(
            "UPDATE seen_jobs SET seen_at = ? WHERE title = ?",
            (time.time() - 2 * 86400, "x"),
        )
        cache.conn.commit()

        new = cache.filter_new([job])
        assert len(new) == 1  # expired, so it's "new" again
        cache.close()

    def test_stats(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        cache = SeenJobsCache(db_path=str(tmp_path / "test.db"), ttl_days=7)

        jobs = [Job(title=str(i), company="C", location="L", url="u", source="S") for i in range(5)]
        cache.mark_seen(jobs)
        stats = cache.stats()
        assert stats["total_entries"] == 5
        assert stats["active_entries"] == 5
        cache.close()

    def test_clear(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        cache = SeenJobsCache(db_path=str(tmp_path / "test.db"), ttl_days=7)

        jobs = [Job(title="X", company="Y", location="Z", url="u", source="S")]
        cache.mark_seen(jobs)
        assert cache.stats()["total_entries"] == 1

        cache.clear()
        assert cache.stats()["total_entries"] == 0
        cache.close()

    def test_context_manager(self, tmp_path):
        from hirevia.cache import SeenJobsCache
        with SeenJobsCache(db_path=str(tmp_path / "test.db")) as cache:
            cache.mark_seen([Job(title="X", company="Y", location="Z", url="u", source="S")])
            assert cache.stats()["total_entries"] == 1
        # Connection closed after context exit
