"""Deterministic Phase 1 quality gate tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hirevia.models import Job
from hirevia.quality import (
    LinkState,
    duplicate_key,
    freshness_score,
    is_expired,
    is_relevant,
    normalize_url,
    is_fresher_eligible,
    is_target_role,
    verify_application_link,
    profile_qualified,
)
from hirevia.models import Profile


def job(title="Python Developer", company="Acme", description="Python Django FastAPI", **metadata):
    return Job(
        title=title,
        company=company,
        location="Remote",
        url=metadata.pop("url", "https://example.com/jobs/1"),
        description=description,
        source_metadata=metadata,
        tags=metadata.pop("tags", []),
        remote=True,
    )


def test_expiration_deadline_and_status_rules():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    assert is_expired(job(deadline="2026-08-21T23:59:00Z"), now)
    assert not is_expired(job(deadline="2026-08-23T00:00:00Z"), now)
    assert is_expired(job(status="closed"), now)
    assert is_expired(job(status="position filled"), now)
    assert not is_expired(job(), now)
    assert not is_expired(job(deadline="not-a-date"), now)


def test_old_posted_date_alone_is_not_expired_and_timezone_is_safe():
    old = job(published_at="2020-01-01T00:00:00Z")
    old.posted = "2020-01-01"
    assert not is_expired(old, datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert is_expired(
        job(deadline="2026-08-22T00:30:00-04:00"),
        datetime(2026, 8, 22, 5, 0, tzinfo=timezone.utc),
    )


def test_freshness_ranges_are_deterministic():
    now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
    assert freshness_score(job(published_at="2026-08-22T11:30:00Z"), now) == 100
    assert freshness_score(job(published_at="2026-08-22T08:00:00Z"), now) == 85
    assert freshness_score(job(published_at="2026-08-22T00:00:00Z"), now) == 70
    assert freshness_score(job(published_at="2026-08-20T12:00:00Z"), now) == 45
    assert freshness_score(job(published_at="2026-08-10T12:00:00Z"), now) == 20
    assert freshness_score(job(published_at="invalid"), now) == 25


def test_url_normalization_and_cross_source_identity():
    first = normalize_url("HTTPS://Example.com/jobs/123/?utm_source=telegram")
    second = normalize_url("https://example.com/jobs/123")
    assert first == second
    assert duplicate_key(job(url="https://example.com/jobs/123?utm_campaign=x")) == duplicate_key(job(url="https://example.com/jobs/123"))
    assert duplicate_key(job(url="https://example.com/jobs/124")) != duplicate_key(job(url="https://example.com/jobs/123"))


def test_relevance_accepts_role_variants_and_rejects_mismatches():
    assert is_relevant("Python Developer", job("Python Engineer"))
    assert is_relevant("Python Developer", job("Backend Engineer - Python"))
    assert is_relevant("Python Developer", job("Django Developer", "Acme", "Django and Python"))
    assert not is_relevant("Python Developer", job("Mechanical Engineer", description="Python appears once in this unrelated automation note."))
    assert not is_relevant("Python Developer", job("Java Developer", description="Java Spring role"))
    assert is_relevant("Data Scientist", job("Applied Scientist", description="Machine learning and data science"))
    assert is_relevant("Frontend Developer", job("React Engineer", description="React and frontend"))


def test_link_verification_states_without_live_network():
    class Response:
        def __init__(self, status):
            self.status_code = status

    class Session:
        def __init__(self, status=None, error=None):
            self.status = status
            self.error = error

        def head(self, *args, **kwargs):
            if self.error:
                raise self.error
            return Response(self.status)

        def get(self, *args, **kwargs):
            return Response(self.status)

    assert verify_application_link("https://example.com/job", session=Session(200)) == LinkState.VERIFIED_ACTIVE
    assert verify_application_link("https://example.com/job", session=Session(404)) == LinkState.VERIFIED_UNAVAILABLE
    assert verify_application_link("https://example.com/job", session=Session(410)) == LinkState.VERIFIED_UNAVAILABLE
    assert verify_application_link("https://example.com/job", session=Session(error=TimeoutError())) == LinkState.UNKNOWN
    assert verify_application_link("not-a-url", session=Session(200)) == LinkState.UNKNOWN


def test_fresher_and_role_gates():
    assert is_fresher_eligible(job("Data Scientist Intern", description="0-2 years experience"))
    assert is_fresher_eligible(job("Graduate Trainee", description="Fresh graduate"))
    assert not is_fresher_eligible(job("Senior Data Engineer", description="5 years experience"))
    assert not is_fresher_eligible(job("Lead Software Engineer", description="3+ years"))
    assert is_target_role(job("Data Analyst Intern"))
    assert is_target_role(job("Backend Developer"))
    assert not is_target_role(job("Sales Associate"))


def test_profile_qualification_requires_india_early_career_and_role():
    profile = Profile(
        target_roles=["Data Scientist", "Machine Learning Engineer", "AI Engineer", "Data Analyst"],
        keywords=["Python", "SQL", "Machine Learning", "Pandas", "TensorFlow"],
        locations=["India", "Pune", "Bengaluru"],
        experience=["internship", "fresher", "0-2 years"],
        exclude_keywords=["Senior", "Lead", "Architect", "Manager", "5+ years"],
    )

    cases = [
        ("AI Engineer", "Pune, India", "Fresher", True),
        ("Machine Learning Engineer", "Bengaluru, India", "0-2 years", True),
        ("Data Scientist Intern", "India", "Internship", True),
        ("AI Engineer", "USA", "Fresher", False),
        ("Data Scientist", "Europe", "0-2 years", False),
        ("Data Analyst", "USA", "Entry level", False),
        ("Senior AI Engineer", "Pune, India", "5+ years", False),
        ("Senior Data Scientist", "Bengaluru, India", "4+ years", False),
        ("Frontend React Developer", "Pune, India", "Fresher", False),
    ]
    for title, location, experience, expected in cases:
        candidate = Job(title, "Example", location, "https://example.test/job", f"{experience}. Python SQL Machine Learning", source_metadata={"experience": experience}, score=80)
        assert profile_qualified(candidate, profile) is expected, title


def test_profile_qualification_keeps_unknown_location_scanned():
    profile = Profile(target_roles=["AI Engineer"], keywords=["Python"], locations=["India"], experience=["fresher"])
    candidate = Job("AI Engineer", "Example", "Unknown", "https://example.test/job", "Fresher Python", source_metadata={"experience": "Fresher"}, score=90)
    assert profile_qualified(candidate, profile) is False
