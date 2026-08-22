"""Deterministic quality gates for discovered Hirevia jobs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}
_TRACKING_PREFIXES = ("utm_",)


class LinkState(str, Enum):
    VERIFIED_ACTIVE = "verified_active"
    VERIFIED_UNAVAILABLE = "verified_unavailable"
    UNKNOWN = "unknown"


def _metadata(job: Any) -> dict:
    return job.get("source_metadata", {}) if isinstance(job, dict) else (job.source_metadata or {})


def _value(job: Any, name: str, default: Any = "") -> Any:
    return job.get(name, default) if isinstance(job, dict) else getattr(job, name, default)


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _explicit_closed(job: Any) -> bool:
    metadata = _metadata(job)
    status = str(metadata.get("status", metadata.get("job_status", ""))).lower().strip()
    if status in {"expired", "closed", "filled", "position filled", "removed", "unavailable", "no longer accepting applications"}:
        return True
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description")).lower()
    return bool(re.search(r"\b(position|job|role)\s+(is\s+)?(filled|closed|expired)\b|no longer accepting applications|applications are closed", text))


def is_expired(job: Any, now: Optional[datetime] = None) -> bool:
    """Return true only for explicit closure or a reliably passed deadline."""
    if _explicit_closed(job):
        return True
    metadata = _metadata(job)
    deadline = next((metadata.get(key) for key in ("deadline", "application_deadline", "expires", "expiry", "expiration_date") if metadata.get(key)), None)
    parsed = parse_datetime(deadline)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return parsed <= current


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return ""
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if key.lower() not in _TRACKING_KEYS and not key.lower().startswith(_TRACKING_PREFIXES)]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except ValueError:
        return ""


def duplicate_key(job: Any) -> str:
    """Stable cross-source identity: URL, source ID, external ID, then fields."""
    metadata = _metadata(job)
    url = normalize_url(str(_value(job, "url", ""))) or normalize_url(str(_value(job, "original_url", "")))
    if url:
        return f"url:{url}"
    for key in ("source_job_id", "job_id", "external_id", "stable_external_id", "telegram_message_url"):
        value = str(metadata.get(key, "")).strip().lower()
        if value:
            source = str(_value(job, "source", "")).strip().lower()
            return f"id:{source}:{value}"
    company = re.sub(r"\s+", " ", str(_value(job, "company", "")).lower().strip())
    title = re.sub(r"\s+", " ", str(_value(job, "title", "")).lower().strip())
    location = re.sub(r"\s+", " ", str(_value(job, "location", "")).lower().strip())
    return f"fields:{company}|{title}|{location}"


def freshness_score(job: Any, now: Optional[datetime] = None) -> int:
    metadata = _metadata(job)
    value = next((metadata.get(key) for key in ("published_at", "published", "posted_at", "updated_at") if metadata.get(key)), None)
    value = value or _value(job, "posted", "")
    timestamp = parse_datetime(value)
    if timestamp is None:
        return 25
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (current - timestamp).total_seconds() / 3600)
    if age_hours < 1:
        return 100
    if age_hours < 6:
        return 85
    if age_hours < 24:
        return 70
    if age_hours <= 72:
        return 45
    return 20


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?", text.lower())


_ROLE_ALIASES = {
    "developer": {"developer", "engineer", "programmer", "software"},
    "engineer": {"engineer", "developer", "programmer", "software"},
    "frontend": {"frontend", "front-end", "react", "angular", "vue", "ui"},
    "backend": {"backend", "back-end", "server", "api"},
    "scientist": {"scientist", "ml", "machine", "applied"},
}

_INTENT_ANCHORS = {
    "python": {"python", "django", "flask", "fastapi"},
    "java": {"java", "spring", "boot", "jvm"},
    "data": {"data", "scientist", "ml", "machine", "analytics", "pandas", "sql"},
    "scientist": {"data", "scientist", "ml", "machine", "applied"},
    "machine": {"machine", "ml", "learning", "tensorflow", "pytorch"},
    "learning": {"learning", "machine", "ml", "tensorflow", "pytorch"},
    "ml": {"ml", "machine", "learning", "tensorflow", "pytorch"},
    "frontend": {"frontend", "front", "react", "angular", "vue", "ui"},
    "react": {"react", "frontend", "front"},
    "angular": {"angular", "frontend", "front"},
    "vue": {"vue", "frontend", "angular"},
}

_JOB_INTENT_TOKENS = {
    "developer", "engineer", "scientist", "frontend", "backend", "fullstack",
    "software", "programmer", "analyst", "manager", "designer", "recruiter",
    "python", "java", "javascript", "typescript", "react", "angular", "vue",
    "django", "flask", "fastapi", "data", "machine", "learning", "ml", "qa",
    "devops", "cloud", "security", "product", "intern", "internship",
}

_FRESHER_POSITIVE = re.compile(r"\b(?:intern(?:ship)?|fresher|fresh graduate|new grad|graduate(?: trainee| engineer trainee)?|trainee|entry[- ]level|junior|associate|campus|off[- ]campus|[012](?:\s*[-–]\s*[012])?\s*years?)\b", re.IGNORECASE)
_SENIOR_NEGATIVE = re.compile(r"\b(?:senior|sr\.?|lead|tech lead|principal|staff|architect|manager|senior manager|director|head|vp)\b|\b(?:[3-9]|10)\+?\s*years?\b", re.IGNORECASE)
_TARGET_ROLE = re.compile(r"\b(?:ai(?:\s*/\s*ml)?|machine learning|data (?:scientist|science|analyst|engineer|engineering)|python|software|backend|full[- ]stack|sde|technology analyst)\b.*\b(?:engineer|developer|scientist|analyst|intern|trainee|associate)\b|\b(?:engineer|developer|scientist|analyst|intern|trainee|sde)\b.*\b(?:ai|ml|machine learning|data|python|software|backend|full[- ]stack|technology)\b", re.IGNORECASE)


def is_fresher_eligible(job: Any) -> bool:
    """Accept only internships and roles requiring approximately 0-2 years."""
    metadata = _metadata(job)
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "posted"))
    text += " " + " ".join(str(metadata.get(key, "")) for key in ("experience", "experience_years", "requirements", "seniority", "employment_type", "tags"))
    if _SENIOR_NEGATIVE.search(text):
        return False
    return bool(_FRESHER_POSITIVE.search(text))


def is_target_role(job: Any) -> bool:
    """Keep technology roles relevant to an early-career technology profile."""
    title = str(_value(job, "title", ""))
    if _TARGET_ROLE.search(title):
        return True
    title_lower = title.lower()
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "tags")).lower()
    return bool(re.search(r"\b(?:developer|engineer|scientist|analyst|intern|trainee)\b", title_lower) and re.search(r"\b(?:python|java|javascript|typescript|django|flask|fastapi|machine learning|artificial intelligence|data|software|backend|frontend|react|angular|vue|api|sql)\b", text))


def profile_match_score(job: Any, profile: Any) -> int:
    """Score deterministic role and keyword alignment from profile.yaml."""
    title = str(_value(job, "title", "")).lower()
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "tags")).lower()
    roles = getattr(profile, "target_roles", []) or getattr(profile, "desired_roles", [])
    keywords = getattr(profile, "keywords", []) or getattr(profile, "skills", [])
    role_hits = sum(1 for role in roles if any(token in title for token in _tokens(role)))
    keyword_hits = sum(1 for keyword in keywords if str(keyword).lower() in text)
    role_score = min(100, round(role_hits * 100 / max(1, min(3, len(roles)))))
    keyword_score = min(100, round(keyword_hits * 100 / max(1, min(5, len(keywords)))))
    return round(role_score * 0.65 + keyword_score * 0.35)


def profile_matches(job: Any, profile: Any) -> bool:
    """Apply the user's YAML role, keyword, and exclusion preferences."""
    metadata = _metadata(job)
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "tags"))
    text += " " + " ".join(str(metadata.get(key, "")) for key in ("experience", "requirements", "seniority"))
    lowered = text.lower()
    exclusions = [str(value).lower() for value in getattr(profile, "exclude_keywords", [])]
    if any(value and value in lowered for value in exclusions):
        return False
    roles = getattr(profile, "target_roles", []) or getattr(profile, "desired_roles", [])
    keywords = getattr(profile, "keywords", []) or getattr(profile, "skills", [])
    if not roles and not keywords:
        return True
    return any(str(role).lower() in lowered for role in roles) or any(str(keyword).lower() in lowered for keyword in keywords)


def relevance_score(query: str, job: Any) -> int:
    """Score query relevance with title dominant over body text."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return 100
    title = " ".join(_tokens(str(_value(job, "title", ""))))
    tags = " ".join(_tokens(" ".join(_value(job, "tags", []) or [])))
    metadata = _metadata(job)
    skills = " ".join(_tokens(str(metadata.get("skills", metadata.get("technologies", "")))))
    body = " ".join(_tokens(str(_value(job, "description", ""))))
    title_tokens = set(_tokens(title))
    skill_tokens = set(_tokens(f"{tags} {skills}"))
    body_tokens = set(_tokens(body))
    # Technology/domain anchors must occur in the title or structured skills.
    # A lone mention deep in a description is deliberately insufficient.
    for token in query_tokens:
        anchors = _INTENT_ANCHORS.get(token)
        structured_skill_tokens = set(_tokens(skills))
        if anchors and not (title_tokens & anchors or structured_skill_tokens & anchors):
            return 0
    negative_roles = {"mechanical", "civil", "electrical", "sales", "hr", "human", "accountant", "accounting"}
    if title_tokens & negative_roles and not (title_tokens & set(query_tokens)):
        return 0
    if "scientist" in query_tokens:
        science_terms = {
            "scientist", "science", "machine", "learning", "ml", "model",
            "modeling", "statistics", "statistical", "experimentation",
            "predictive", "tensorflow", "pytorch",
        }
        science_evidence = (body_tokens | set(_tokens(skills))) & science_terms
        direct_scientist_title = title_tokens & {"scientist", "applied", "ml"}
        adjacent_title = title_tokens & {
            "engineer", "engineering", "analyst", "platform", "center",
            "director", "manager",
        }
        if adjacent_title and not direct_scientist_title and len(science_evidence) < 2:
            return 0
    technology_conflicts = {
        "python": {"java", "csharp", "c++", "php", "ruby"},
        "java": {"python", "php", "ruby"},
        "react": {"angular", "vue"},
        "angular": {"react", "vue"},
        "vue": {"react", "angular"},
    }
    for query_token, conflicts in technology_conflicts.items():
        if query_token in query_tokens and title_tokens & conflicts and query_token not in title_tokens and query_token not in skill_tokens:
            return 0
    matched = 0.0
    for token in query_tokens:
        aliases = _ROLE_ALIASES.get(token, {token})
        if title_tokens & aliases:
            matched += 2.5
        elif skill_tokens & aliases:
            matched += 1.8
        elif body_tokens & aliases:
            matched += 0.5
    score = int(round(100 * matched / (2.5 * len(query_tokens))))
    # Multi-word role queries require a meaningful title/skill signal, not one body mention.
    if len(query_tokens) > 1 and matched < 2.5:
        return min(score, 20)
    return min(100, score)


def is_relevant(query: str, job: Any, threshold: int = 45) -> bool:
    # A one-word query is intentionally broad discovery; role phrases use the
    # stronger title/skills gate to prevent unrelated results.
    tokens = _tokens(query)
    if not query.strip():
        return True
    if not any(token in _JOB_INTENT_TOKENS for token in tokens):
        return False
    if not is_target_role(job):
        return False
    return len(tokens) < 2 or relevance_score(query, job) >= threshold


def verify_application_link(url: str, timeout: float = 3.0, retries: int = 1, session: Any = requests) -> LinkState:
    """Verify links conservatively; failures caused by the network remain unknown."""
    normalized = normalize_url(url)
    if not normalized:
        return LinkState.UNKNOWN
    if session is requests and urlsplit(normalized).netloc in {"example.com", "example.org", "example.net"}:
        return LinkState.UNKNOWN
    for attempt in range(retries + 1):
        try:
            response = session.head(normalized, allow_redirects=True, timeout=timeout)
            if response.status_code in {405, 403}:
                response = session.get(normalized, allow_redirects=True, timeout=timeout, stream=True)
            if response.status_code in {404, 410}:
                return LinkState.VERIFIED_UNAVAILABLE
            if 200 <= response.status_code < 400:
                return LinkState.VERIFIED_ACTIVE
            if attempt == retries:
                return LinkState.UNKNOWN
        except (requests.RequestException, OSError):
            if attempt == retries:
                return LinkState.UNKNOWN
    return LinkState.UNKNOWN


def is_likely_application_url(url: str) -> bool:
    normalized = normalize_url(url)
    if not normalized:
        return False
    text = normalized.lower()
    return any(marker in text for marker in (
        "/job", "/career", "/careers", "/apply", "greenhouse.io",
        "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
    ))
