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

_FRESHER_POSITIVE = re.compile(r"\b(?:intern(?:ship)?|fresher|freshers|fresh graduate|new grad|recent graduate|graduate(?:s| trainee| engineer trainee)?|trainee|entry[- ]level|junior|early career|associate|campus|off[- ]campus|0\s*(?:[-–]\s*[12])?\s*years?|1\s*[-–]\s*2\s*years?)\b", re.IGNORECASE)
_SENIOR_NEGATIVE = re.compile(r"\b(?:senior|sr\.?|lead|tech lead|principal|staff|architect|manager|senior manager|director|head|vp)\b|\b(?:[3-9]|10)\s*\+\s*years?\b", re.IGNORECASE)
_TARGET_ROLE = re.compile(r"\b(?:ai(?:\s*/\s*ml)?|machine learning|data (?:scientist|science|analyst|engineer|engineering)|python|software|backend|full[- ]stack|sde|technology analyst)\b.*\b(?:engineer|developer|scientist|analyst|intern|trainee|associate)\b|\b(?:engineer|developer|scientist|analyst|intern|trainee|sde)\b.*\b(?:ai|ml|machine learning|data|python|software|backend|full[- ]stack|technology)\b", re.IGNORECASE)


def is_fresher_eligible(job: Any) -> bool:
    """Return whether the job is explicitly early-career or not senior.

    Missing experience is intentionally accepted; this is used as score
    evidence, not as a discovery gate.
    """
    metadata = _metadata(job)
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "posted"))
    text += " " + " ".join(str(metadata.get(key, "")) for key in ("experience", "experience_years", "requirements", "seniority", "employment_type", "tags"))
    if _SENIOR_NEGATIVE.search(text):
        return False
    return not _SENIOR_NEGATIVE.search(text)


def has_early_career_evidence(job: Any) -> bool:
    """Return whether the listing explicitly targets early-career candidates."""
    metadata = _metadata(job)
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "posted"))
    text += " " + " ".join(str(metadata.get(key, "")) for key in ("experience", "experience_years", "requirements", "seniority", "employment_type", "tags"))
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
    """Return an explainable 0-100 profile score without AND filters."""
    title = str(_value(job, "title", ""))
    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "tags")).lower()
    roles = getattr(profile, "target_roles", []) or getattr(profile, "desired_roles", [])
    keywords = getattr(profile, "keywords", []) or getattr(profile, "skills", [])
    title_tokens = set(_tokens(title))
    role_hits = [role for role in roles if role_match(title, str(role))]
    keyword_hits = [keyword for keyword in keywords if str(keyword).lower() in text]
    score = 50 if role_hits else 0
    score += min(20, len(keyword_hits) * 5)
    locations = getattr(profile, "locations", [])
    location = str(_value(job, "location", "")).lower()
    if any(str(value).lower() in location for value in locations):
        score += 10
    experience_text = text + " " + " ".join(str(_metadata(job).get(key, "")) for key in ("experience", "requirements", "seniority"))
    if _FRESHER_POSITIVE.search(experience_text):
        score += 10
    if _SENIOR_NEGATIVE.search(experience_text):
        score -= 30
    if not title_tokens:
        score = 0
    return max(0, min(100, score))


def role_match(title: str, role: str) -> bool:
    """Match common role variations while requiring meaningful title terms."""
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    wanted = re.sub(r"[^a-z0-9]+", " ", role.lower()).strip()
    aliases = {
        "artificial intelligence": "ai", "machine learning": "ml",
        "software development engineer": "sde", "backend software": "backend",
        "python software": "python", "machine learning developer": "ml",
    }
    for source, target in aliases.items():
        normalized = normalized.replace(source, target)
        wanted = wanted.replace(source, target)
    if wanted == "sde":
        return bool(re.search(r"\b(?:sde|software (?:development|) engineer|software engineer)\b", normalized))
    wanted_tokens = set(wanted.split())
    if wanted_tokens <= {"ai", "engineer"}:
        return bool(re.search(r"\b(?:ai|ml)\b", normalized) and re.search(r"\b(?:engineer|developer)\b", normalized))
    if "data scientist" in wanted:
        return bool(re.search(r"\b(?:data scientist|applied scientist)\b", normalized))
    if "data analyst" in wanted:
        return bool(re.search(r"\bdata analyst\b", normalized))
    if "backend" in wanted:
        return "backend" in normalized and bool(re.search(r"\b(?:engineer|developer|software)\b", normalized))
    if "python" in wanted:
        return "python" in normalized and bool(re.search(r"\b(?:developer|engineer|software)\b", normalized))
    if "machine learning" in role.lower() or wanted == "ml engineer":
        return bool(re.search(r"\b(?:machine learning|ml)\b", normalized) and re.search(r"\b(?:engineer|developer)\b", normalized))
    return bool(wanted_tokens and wanted_tokens <= set(normalized.split()))


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


def profile_qualified(job: Any, profile: Any, threshold: int = 50) -> bool:
    """Apply the single authoritative qualification rule for MATCHED jobs."""
    score = int(_value(job, "score", 0) or 0)
    if score < threshold:
        return False

    metadata = _metadata(job)
    location = str(_value(job, "location", "")).strip().lower()
    structured_location = " ".join(
        str(_value(job, key, "")) for key in ("location", "country", "timezone", "location_restrictions")
    ).lower()
    # Global/foreign-only locations are never confirmed profile matches.
    if re.search(r"\b(?:usa?|united states|europe|latam|uk|canada|australia|germany|singapore|remote\s+(?:global|usa?|europe))\b", structured_location):
        if not re.search(r"\b(?:india|indian|pan\s*india|pune|mumbai|bangalore|bengaluru|hyderabad|chennai|delhi|noida|gurgaon|gurugram|ahmedabad|kolkata|jaipur)\b", structured_location):
            return False
    if str(_value(job, "india_eligibility", "UNKNOWN")) != "INDIA_ELIGIBLE":
        return False
    if not location or location in {"unknown", "n/a", "na", "not specified"}:
        return False

    if _SENIOR_NEGATIVE.search(" ".join(str(metadata.get(key, "")) for key in ("experience", "experience_years", "requirements", "seniority")) + " " + str(_value(job, "title", ""))):
        return False
    if not has_early_career_evidence(job):
        return False

    title = str(_value(job, "title", ""))
    roles = getattr(profile, "target_roles", []) or getattr(profile, "desired_roles", [])
    if not any(role_match(title, str(role)) for role in roles):
        return False

    text = " ".join(str(_value(job, key, "")) for key in ("title", "description", "tags"))
    text += " " + " ".join(str(metadata.get(key, "")) for key in ("experience", "requirements", "seniority"))
    exclusions = [str(value).strip() for value in getattr(profile, "exclude_keywords", [])]
    if any(value and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE) for value in exclusions):
        return False
    return True


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
        "lever.co", "workable.com", "smartrecruiters.com",
    ))
