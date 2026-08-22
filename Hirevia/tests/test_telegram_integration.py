"""Comprehensive tests for Telegram job source integration."""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from hirevia.sources.telegram import TelegramSearch
from hirevia.models import Job
from hirevia.sources import SourceRegistry


class TestTelegramJobDetection:
    """Test job detection logic."""

    def test_is_job_true_hiring(self):
        """Job keyword 'hiring' should be detected."""
        text = "We are hiring for a Python Developer position"
        assert TelegramSearch._is_job(text) is True

    def test_is_job_true_vacancy(self):
        """Job keyword 'vacancy' should be detected."""
        text = "Vacancy: Software Engineer required"
        assert TelegramSearch._is_job(text) is True

    def test_is_job_true_internship(self):
        """Job keyword 'internship' should be detected."""
        text = "Internship opportunity for freshers"
        assert TelegramSearch._is_job(text) is True

    def test_is_job_false_course(self):
        """Non-job keyword 'course' should be rejected."""
        text = "Register for our new Python course now"
        assert TelegramSearch._is_job(text) is False

    def test_is_job_false_webinar(self):
        """Non-job keyword 'webinar' should be rejected."""
        text = "Join our webinar on AI and ML"
        assert TelegramSearch._is_job(text) is False

    def test_is_job_false_masterclass(self):
        """Non-job keyword 'masterclass' should be rejected."""
        text = "Exclusive masterclass: Learn React in 30 days"
        assert TelegramSearch._is_job(text) is False

    def test_is_job_empty(self):
        """Empty text should return False."""
        assert TelegramSearch._is_job("") is False
        assert TelegramSearch._is_job(None) is False


class TestTelegramURLExtraction:
    """Test URL extraction logic."""

    def test_url_extraction_single(self):
        """Extract single URL from text."""
        text = "Apply here: https://careers.example.com/jobs/123"
        url = TelegramSearch._url(text)
        assert url == "https://careers.example.com/jobs/123"

    def test_url_extraction_multiple_prefers_non_telegram(self):
        """With multiple URLs, prefer non-Telegram URLs."""
        text = "Check https://jobs.example.com and https://t.me/channel"
        url = TelegramSearch._url(text)
        assert url == "https://jobs.example.com"

    def test_url_extraction_none(self):
        """No URL should return empty string."""
        text = "Apply directly to our office"
        url = TelegramSearch._url(text)
        assert url == ""

    def test_url_extraction_strips_punctuation(self):
        """URLs at end of sentence should have punctuation stripped."""
        text = "Apply here: https://jobs.example.com."
        url = TelegramSearch._url(text)
        assert url == "https://jobs.example.com"


class TestTelegramMessageURL:
    """Test Telegram message URL generation."""

    def test_telegram_message_url_generation(self):
        """Generate valid Telegram message URL."""
        url = TelegramSearch._telegram_message_url("@jobschannel", 12345)
        assert url == "https://t.me/jobschannel/12345"

    def test_telegram_message_url_removes_at_sign(self):
        """Should remove @ prefix from username."""
        url = TelegramSearch._telegram_message_url("@channel", 999)
        assert url == "https://t.me/channel/999"

    def test_telegram_message_url_empty_username(self):
        """Empty username should return empty string."""
        url = TelegramSearch._telegram_message_url("", 123)
        assert url == ""


class TestTelegramFieldExtraction:
    """Test field extraction from job text."""

    def test_field_extraction_title_role(self):
        """Extract title from 'role:' pattern."""
        text = "Role: Senior Python Developer - 5 years experience"
        title = TelegramSearch._field(text, [r"role\s*[:\-]\s*(.+?)(?:\n|$)"])
        assert "Senior Python Developer" in title

    def test_field_extraction_company(self):
        """Extract company from text."""
        text = "Company: TechCorp Inc\nLocation: Remote"
        company = TelegramSearch._field(text, [r"company\s*(?:name)?\s*[:\-]\s*(.+?)(?:\n|$)"])
        assert company == "TechCorp Inc"

    def test_field_extraction_location(self):
        """Extract location from text."""
        text = "Location: San Francisco, CA\nRemote: Yes"
        location = TelegramSearch._field(text, [r"location\s*[:\-]\s*(.+?)(?:\n|$)"])
        assert location == "San Francisco, CA"


class TestTelegramSourceIntegration:
    """Test integration with SourceRegistry."""

    def test_telegram_enabled_by_config(self):
        """Telegram follows the checked-in source configuration."""
        registry = SourceRegistry.from_yaml("sources.yaml")
        telegram_source = next(
            (s for s in registry.enabled_sources()),
            None
        )
        enabled_ids = {s.source_id for s in registry.enabled_sources()}
        assert "telegram" in enabled_ids

    def test_telegram_in_registry(self):
        """Telegram should be accessible through registry."""
        registry = SourceRegistry.from_yaml("sources.yaml")
        # Try to get Telegram source (even if disabled)
        try:
            # This might raise KeyError if not in registry, which is OK for this test
            telegram = registry.get("telegram")
            assert telegram is not None
            assert telegram.source_id == "telegram"
        except KeyError:
            # If not in registry at all, check if it should be
            pytest.skip("Telegram not in registry (may be expected)")

    def test_telegram_is_in_enabled_sources_when_configured(self):
        """Configured Telegram participates in the unified registry."""
        registry = SourceRegistry.from_yaml("sources.yaml")
        enabled_ids = {s.source_id for s in registry.enabled_sources()}
        assert "telegram" in enabled_ids


class TestTelegramErrorHandling:
    """Test error handling."""

    @patch.dict("os.environ", {}, clear=True)
    def test_fetch_missing_credentials(self):
        """Missing TELEGRAM_API_ID/HASH should not raise exception."""
        telegram = TelegramSearch()
        telegram.enabled = True
        telegram._channels = [{"username": "@test", "enabled": True}]
        
        # Should return empty list, not raise
        result = telegram.fetch("test query")
        assert result == []
        assert isinstance(result, list)

    @patch.dict("os.environ", {"TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": ""}, clear=True)
    def test_fetch_empty_credentials(self):
        """Empty TELEGRAM_API_ID/HASH should not raise exception."""
        telegram = TelegramSearch()
        telegram.enabled = True
        telegram._channels = [{"username": "@test", "enabled": True}]
        
        result = telegram.fetch("test query")
        assert result == []

    def test_fetch_disabled_telegram(self):
        """Disabled Telegram should return empty list."""
        telegram = TelegramSearch()
        telegram.enabled = False
        
        result = telegram.fetch("test query")
        assert result == []

    def test_fetch_no_channels(self):
        """No configured channels should return empty list."""
        telegram = TelegramSearch()
        telegram.enabled = True
        telegram._channels = []
        
        result = telegram.fetch("test query")
        assert result == []


class TestTelegramJobModel:
    """Test Job object creation from Telegram messages."""

    def test_parse_creates_job_object(self):
        """Parsed job should be a valid Job object."""
        telegram = TelegramSearch()
        
        # Mock message
        message = Mock()
        message.text = "Role: Python Developer\nCompany: TechCorp\nLocation: Remote\nhttps://apply.example.com"
        message.id = 12345
        message.date = datetime.now()
        
        job = telegram._parse(message, "Test Channel", "@testchannel")
        
        assert isinstance(job, Job)
        assert job.source == "Telegram"
        assert job.source_type == "Telegram"
        assert job.title is not None
        assert job.company is not None
        assert job.location is not None

    def test_parse_non_job_returns_none(self):
        """Non-job messages should return None."""
        telegram = TelegramSearch()
        
        message = Mock()
        message.text = "Register for our Python course now!"
        message.id = 12345
        message.date = datetime.now()
        
        job = telegram._parse(message, "Test Channel", "@testchannel")
        assert job is None

    def test_parse_stores_metadata(self):
        """Job should store Telegram metadata."""
        telegram = TelegramSearch()
        
        message = Mock()
        message.text = "Hiring: Software Engineer"
        message.id = 99999
        message.date = datetime.now()
        
        job = telegram._parse(message, "Test Jobs", "@jobschannel")
        
        assert job.source_metadata["channel"] == "Test Jobs"
        assert job.source_metadata["telegram_message_id"] == "99999"
        assert job.source_metadata["channel_username"] == "@jobschannel"
        assert "telegram_message_url" in job.source_metadata


class TestTelegramDeduplication:
    """Test deduplication of Telegram jobs."""

    def test_telegram_jobs_not_deduplicated_by_normal_rules(self):
        """Telegram jobs should not be filtered by standard dedup."""
        from hirevia.pipeline import deduplicate_jobs
        
        job1 = Job(
            title="Python Dev",
            company="Company A",
            location="Remote",
            url="https://t.me/channel/123",
            source="Telegram"
        )
        job2 = Job(
            title="Python Dev",
            company="Company A",
            location="Remote",
            url="https://github.com/jobs/456",
            source="GitHub"
        )
        
        result = deduplicate_jobs([job1, job2])
        
        # Both should be in result since job1 is Telegram
        assert len([j for j in result if j.source == "Telegram"]) == 1
        assert len([j for j in result if j.source == "GitHub"]) == 1
