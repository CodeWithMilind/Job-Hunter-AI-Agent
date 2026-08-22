"""Reusable OpenAI-compatible NVIDIA client for three-stage job relevance."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
_QUERY_CACHE: dict[str, "NVIDIAQueryIntent"] = {}
_JOB_CACHE: dict[tuple[str, str], "NVIDIAJobAnalysis"] = {}
_RANK_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


@dataclass
class NVIDIAQueryIntent:
    original_query: str
    primary_role: str = ""
    seniority: str = "any"
    technologies: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    job_type: str = "any"
    location: str = "any"
    remote: str = "any"
    confidence: float = 0.0


@dataclass
class NVIDIAJobAnalysis:
    relevant: bool
    relevance_score: int
    role_match: int = 0
    skill_match: int = 0
    seniority_match: int = 0
    domain_match: int = 0
    location_match: int = 0
    technology_match: int = 0
    matched_requirements: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    hard_mismatch: bool = False


class NVIDIAClient:
    """Single HTTP implementation for query, job, and ranking models."""

    def __init__(self, timeout: float | None = None):
        self.api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        self.base_url = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.query_model = os.environ.get("QUERY_MODEL", "").strip()
        self.job_model = os.environ.get("JOB_ANALYSIS_MODEL", "").strip()
        self.ranking_model = os.environ.get("RANKING_MODEL", "").strip()
        self.timeout = timeout or float(os.environ.get("LLM_TIMEOUT", os.environ.get("LLM_TIMEOUT_SECONDS", "20")))
        self.max_candidates = max(1, int(os.environ.get("LLM_MAX_CANDIDATES", os.environ.get("LLM_CANDIDATE_LIMIT", "30"))))
        self.enabled = os.environ.get("LLM_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.available = bool(self.enabled and self.api_key and self.query_model and self.job_model and self.ranking_model)
        self.last_error = ""

    def _complete(self, model: str, system: str, user: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        stage = "query analysis" if model == self.query_model else "job analysis" if model == self.job_model else "ranking"
        started = time.perf_counter()
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": 0,
                    "max_tokens": 384,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("NVIDIA response was not a JSON object")
            logger.info("NVIDIA %s: success (%.2fs)", stage, time.perf_counter() - started)
            return parsed
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            self.last_error = str(exc)[:240]
            logger.warning("NVIDIA %s: failure (%.2fs): %s", stage, time.perf_counter() - started, self.last_error)
            return None

    def analyze_query(self, query: str, location: str = "") -> NVIDIAQueryIntent:
        key = "|".join(" ".join(query.lower().split()) for _ in (0,)) + "|" + " ".join(location.lower().split())
        if key in _QUERY_CACHE:
            return _QUERY_CACHE[key]
        fallback = NVIDIAQueryIntent(original_query=query, primary_role=query, location=location or "any")
        data = self._complete(
            self.query_model,
            """You are Hirevia's Query Analysis Model. Understand a user's job-search query and convert it into precise structured intent. Identify target role, seniority, technologies, required and preferred skills, domain, location, remote preference, useful keywords, and exclusions. Do not broaden the query unnecessarily. Python Developer must remain Python-focused. Data Scientist is not automatically Data Engineer, Data Analyst, Data Platform Engineer, or Data Center Engineer. Return ONLY valid JSON matching the requested schema.""",
            f"Return JSON with fields original_query, primary_role, seniority, technologies, required_skills, preferred_skills, domains, keywords, exclude_terms, job_type, location, remote, confidence for query {query!r} and location {location!r}.",
        )
        if data:
            fallback = NVIDIAQueryIntent(
                original_query=str(data.get("original_query", query)),
                primary_role=str(data.get("primary_role", query)),
                seniority=str(data.get("seniority", "any")),
                technologies=self._strings(data.get("technologies")),
                required_skills=self._strings(data.get("required_skills")),
                preferred_skills=self._strings(data.get("preferred_skills")),
                domains=self._strings(data.get("domains")),
                keywords=self._strings(data.get("keywords")),
                exclude_terms=self._strings(data.get("exclude_terms")),
                job_type=str(data.get("job_type", "any")),
                location=str(data.get("location", location or "any")),
                remote=str(data.get("remote", "any")),
                confidence=self._number(data.get("confidence")),
            )
        _QUERY_CACHE[key] = fallback
        return fallback

    def analyze_jobs(self, query: str, intent: NVIDIAQueryIntent, jobs: Iterable[Any]) -> dict[str, NVIDIAJobAnalysis]:
        results: dict[str, NVIDIAJobAnalysis] = {}
        for job in list(jobs)[: self.max_candidates]:
            identity = self.job_identity(job)
            cache_key = (query.lower().strip(), identity)
            if cache_key in _JOB_CACHE:
                results[identity] = _JOB_CACHE[cache_key]
                continue
            data = self._complete(
                self.job_model,
                """You are Hirevia's Job Analysis Model. Determine whether a job is genuinely relevant to the structured search intent. Inspect title and actual content, skills, technologies, responsibilities, seniority, location, and employment type. Generic words such as developer, engineer, software, data, or AI are not evidence alone. Related roles are not equivalent. Be conservative and return ONLY valid JSON.""",
                f"Search intent: {json.dumps(intent.__dict__, ensure_ascii=True)}\nJob: {json.dumps(self.job_public_data(job), ensure_ascii=True)}\nReturn JSON with relevant, relevance_score, role_match, skill_match, seniority_match, domain_match, location_match, technology_match, matched_requirements, missing_requirements, reasons, hard_mismatch.",
            )
            if not data or not isinstance(data.get("relevant"), bool) or not isinstance(data.get("relevance_score"), (int, float)):
                continue
            result = NVIDIAJobAnalysis(
                relevant=bool(data["relevant"]),
                relevance_score=max(0, min(100, int(data["relevance_score"]))),
                role_match=self._score(data.get("role_match")),
                skill_match=self._score(data.get("skill_match")),
                seniority_match=self._score(data.get("seniority_match")),
                domain_match=self._score(data.get("domain_match")),
                location_match=self._score(data.get("location_match")),
                technology_match=self._score(data.get("technology_match")),
                matched_requirements=self._strings(data.get("matched_requirements")),
                missing_requirements=self._strings(data.get("missing_requirements")),
                reasons=self._strings(data.get("reasons")),
                hard_mismatch=bool(data.get("hard_mismatch", False)),
            )
            _JOB_CACHE[cache_key] = result
            results[identity] = result
        return results

    def rank_jobs(self, query: str, intent: NVIDIAQueryIntent, jobs: Iterable[Any], analyses: dict[str, NVIDIAJobAnalysis]) -> dict[str, dict[str, Any]]:
        candidates = list(jobs)[: self.max_candidates]
        cache_key = hashlib.sha256((query + "|" + "|".join(sorted(analyses))).encode()).hexdigest()
        if cache_key in _RANK_CACHE:
            return _RANK_CACHE[cache_key]
        payload = [{"job_id": self.job_identity(job), "title": job.title, "company": job.company, "analysis": analyses[self.job_identity(job)].__dict__, "quality": {"freshness": (job.source_metadata or {}).get("freshness_score", 25), "application": (job.source_metadata or {}).get("application_link_state", "unknown")}, "source": job.source} for job in candidates if self.job_identity(job) in analyses]
        data = self._complete(
            self.ranking_model,
            """You are Hirevia's Final Ranking Model. Rank candidates for the user's search. Prioritize exact role, required technology, skills, seniority, domain, location, freshness, application availability, and quality. Do not rediscover or broaden intent. Remove weak or unrelated jobs. Return ONLY valid JSON.""",
            f"Query: {query}\nIntent: {json.dumps(intent.__dict__, ensure_ascii=True)}\nCandidates: {json.dumps(payload, ensure_ascii=True)}\nReturn {{\"ranked_jobs\":[{{\"job_id\":\"...\",\"rank\":1,\"final_score\":94,\"reason\":\"short\"}}]}}",
        )
        ranked = {}
        if data and isinstance(data.get("ranked_jobs"), list):
            for item in data["ranked_jobs"]:
                if isinstance(item, dict) and item.get("job_id") in analyses:
                    ranked[str(item["job_id"])] = {"final_score": self._score(item.get("final_score")), "rank": item.get("rank", 999), "reason": str(item.get("reason", ""))[:300]}
        _RANK_CACHE[cache_key] = ranked
        return ranked

    @staticmethod
    def job_identity(job: Any) -> str:
        metadata = getattr(job, "source_metadata", {}) or {}
        return str(metadata.get("source_job_id") or metadata.get("telegram_message_url") or getattr(job, "url", "") or f"{job.company}|{job.title}|{job.location}").lower().strip()

    @staticmethod
    def job_public_data(job: Any) -> dict[str, Any]:
        return {"title": job.title, "company": job.company, "location": job.location, "description": str(job.description)[:1200], "skills": job.tags[:20], "source": job.source, "posted": job.posted, "remote": job.remote}

    @staticmethod
    def _strings(value: Any) -> list[str]:
        return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []

    @staticmethod
    def _score(value: Any) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
