"""Optional local-LLM query understanding and semantic relevance."""

from __future__ import annotations

import json
import logging
import os
import re
import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "qwen3:1.7b"
_QUERY_CACHE: dict[str, "SearchIntent"] = {}
_RESULT_CACHE: dict[tuple[str, str], "LLMJobResult"] = {}


@dataclass
class SearchIntent:
    original_query: str
    target_roles: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    related_roles: list[str] = field(default_factory=list)
    excluded_roles: list[str] = field(default_factory=list)
    seniority: str = ""
    location: str = ""
    remote_preference: str = ""
    employment_type: str = ""
    confidence: float = 0.0


@dataclass
class LLMJobResult:
    relevant: bool
    score: int
    reason: str = ""
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    confidence: float = 0.0


class LLMRelevance:
    """Small, bounded client for the project's local LLM endpoints."""

    def __init__(self, base_url: str = "", model: str = "", timeout: float | None = None):
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or os.environ.get("LLM_URL", "")
        self.model = model or os.environ.get("LLM_MODEL", "") or _DEFAULT_MODEL
        self.timeout = timeout or float(os.environ.get("LLM_TIMEOUT_SECONDS", "20"))
        self.enabled = os.environ.get("LLM_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.available = bool(self.enabled and self.base_url)
        self._active_system_prompt = self._query_system_prompt()

    @staticmethod
    def _clean_json(content: str) -> dict[str, Any] | None:
        content = re.sub(r"<think>.*?</think>|<think>.*", "", content or "", flags=re.DOTALL).strip()
        candidates = [content, re.sub(r"```(?:json)?\s*|```", "", content).strip()]
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                continue
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def _call(self, prompt: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self._active_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 96},
                    "keep_alive": "10m",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            message = payload.get("message", {}) if isinstance(payload, dict) else {}
            return self._clean_json(message.get("content", ""))
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            logger.warning("LLM relevance unavailable: %s", exc)
            return None

    @staticmethod
    def _query_system_prompt() -> str:
        return """You are Hirevia's Job Search Intent Analyzer. Identify the primary role,
    genuine variations, skills, related roles, and exclusions without broadening the query.
    Generic engineer/developer/data/software words are insufficient. Data Scientist is not
    automatically Data Engineer, Data Analyst, Data Platform Engineer, or Data Center
    Engineer. Mark meaningless queries low confidence. Return only JSON with original_query,
    target_roles, required_skills, preferred_skills, related_roles, excluded_roles, seniority,
    location, remote_preference, employment_type, confidence."""

    @staticmethod
    def _job_system_prompt() -> str:
        return """You are Hirevia's Job Relevance Evaluator. Compare the job with the search
    intent using title and actual content, not generic shared words. Related roles are not
    equivalent: Data Scientist is not automatically Data Engineer or Data Analyst; Data
    Center Engineer is not Data Scientist; Java-only is not Python Developer; Store Manager
    is not Python Developer. Require substantial matching responsibilities or skills. Prefer
    precision, never invent facts, and return only JSON with relevant, score, reason,
    matched_skills, missing_skills, confidence."""

    def understand_query(self, query: str, location: str = "") -> SearchIntent:
        normalized = " ".join(query.lower().split()) + "|" + " ".join(location.lower().split())
        fallback = SearchIntent(original_query=query, target_roles=[query] if query.strip() else [], location=location)
        if not self.available:
            return fallback
        if normalized in _QUERY_CACHE:
            return _QUERY_CACHE[normalized]
        self._active_system_prompt = self._query_system_prompt()
        data = self._call(
            f"Return JSON intent for job query {query!r}: original_query, target_roles, "
            "required_skills, preferred_skills, related_roles, excluded_roles, "
            "seniority, location, remote_preference, employment_type, confidence. "
            "Do not broaden or invent requirements."
        )
        if data:
            fallback = SearchIntent(
                original_query=str(data.get("original_query", query)),
                target_roles=self._strings(data.get("target_roles"), [query]),
                required_skills=self._strings(data.get("required_skills")),
                preferred_skills=self._strings(data.get("preferred_skills")),
                related_roles=self._strings(data.get("related_roles")),
                excluded_roles=self._strings(data.get("excluded_roles")),
                seniority=str(data.get("seniority", "")),
                location=str(data.get("location", location)),
                remote_preference=str(data.get("remote_preference", "")),
                employment_type=str(data.get("employment_type", "")),
                confidence=self._number(data.get("confidence", 0)),
            )
        _QUERY_CACHE[normalized] = fallback
        return fallback

    def evaluate_jobs(self, query: str, intent: SearchIntent, jobs: Iterable[Any], limit: int = 30) -> dict[str, LLMJobResult]:
        results: dict[str, LLMJobResult] = {}
        candidates = list(jobs)[:limit]
        for job in candidates:
            key = self.job_key(query, job)
            cached = _RESULT_CACHE.get(key)
            if cached:
                results[key[1]] = cached
                continue
            self._active_system_prompt = self._job_system_prompt()
            data = self._call(self._job_prompt(query, intent, job))
            if not data:
                continue
            if not isinstance(data.get("relevant"), bool) or not isinstance(data.get("score"), (int, float)):
                continue
            result = LLMJobResult(
                relevant=bool(data.get("relevant", False)),
                score=max(0, min(100, int(data.get("score", 0)))),
                reason=str(data.get("reason", ""))[:300],
                matched_skills=self._strings(data.get("matched_skills")),
                missing_skills=self._strings(data.get("missing_skills")),
                confidence=self._number(data.get("confidence", 0)),
            )
            _RESULT_CACHE[key] = result
            results[key[1]] = result
        return results

    @staticmethod
    def job_key(query: str, job: Any) -> tuple[str, str]:
        metadata = getattr(job, "source_metadata", {}) or {}
        identity = metadata.get("source_job_id") or metadata.get("telegram_message_url") or getattr(job, "url", "")
        identity = str(identity or f"{getattr(job, 'company', '')}|{getattr(job, 'title', '')}|{getattr(job, 'location', '')}").lower().strip()
        content = "|".join([
            str(getattr(job, "title", "")),
            str(getattr(job, "company", "")),
            str(getattr(job, "description", "")),
            ",".join(getattr(job, "tags", []) or []),
        ])
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return (" ".join(query.lower().split()), f"{identity}:{fingerprint}")

    @staticmethod
    def _strings(value: Any, default: list[str] | None = None) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return default or []

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _job_prompt(query: str, intent: SearchIntent, job: Any) -> str:
        tags = ", ".join(getattr(job, "tags", [])[:12])
        description = str(getattr(job, "description", ""))[:700]
        return f"""Evaluate job relevance for query {query!r}. Return JSON only:
{{"relevant": true/false, "score": 0-100, "reason": "short", "matched_skills": [], "missing_skills": [], "confidence": 0-1}}
Intent: target roles={intent.target_roles}; required skills={intent.required_skills}; excluded roles={intent.excluded_roles}
Job title={getattr(job, 'title', '')}; company={getattr(job, 'company', '')}; location={getattr(job, 'location', '')}; tags={tags}; description={description}"""
