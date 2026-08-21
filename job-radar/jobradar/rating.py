"""AI Rating engine — scores jobs against a user profile via local LLM."""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

from jobradar.models import Job, Profile

logger = logging.getLogger(__name__)

LLM_URL = os.environ.get("LLM_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# Common LLM server ports — detection tries these in order
_DEFAULT_PORTS = [
    ("http://localhost:11434", "Ollama"),       # Ollama default
    ("http://localhost:8080",  "llama.cpp"),    # llama.cpp default
    ("http://localhost:1234",  "LM Studio"),    # LM Studio default
]

# Default model — use qwen3:1.7b (fast, small, good for CPU)
_OLLAMA_MODEL = "qwen3:1.7b"
_LLAMACPP_MODEL = "qwen3-1.7b"


class AIRater:
    """Rate jobs against a profile using a local LLM (OpenAI-compatible API)."""

    def __init__(self, base_url: str = "", max_concurrency: int = 3):
        self.base_url = base_url or LLM_URL
        self.max_concurrency = max_concurrency
        self.backend = self._detect_backend()
        self.available = self.backend is not None
        if self.available:
            global LLM_MODEL
            # Auto-detect model name
            if not LLM_MODEL:
                if self.backend == "ollama":
                    LLM_MODEL = self._detect_ollama_model() or _OLLAMA_MODEL
                else:
                    LLM_MODEL = _LLAMACPP_MODEL
            logger.info("LLM backend: %s (model: %s)", self.backend, LLM_MODEL)

    def _detect_ollama_model(self) -> Optional[str]:
        """Pick the best available model from Ollama.

        Default: qwen3:1.7b. Falls back to smallest model if not found.
        Users can override with --llm-model flag.
        """
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            if r.status_code == 200:
                models = r.json().get("models", [])
                if models:
                    available = {m.get("name", "") for m in models}
                    # Prefer our default model
                    if _OLLAMA_MODEL in available:
                        return _OLLAMA_MODEL
                    # Fall back to smallest model by size
                    smallest = min(models, key=lambda m: m.get("size", float("inf")))
                    return smallest.get("name", "")
        except Exception:
            pass
        return None

    def _detect_backend(self) -> Optional[str]:
        """Detect whether we're talking to Ollama, llama.cpp, or nothing.

        If a specific URL was given, try that first. Then fall back to
        scanning common localhost ports (11434, 8080, 1234).
        Also measures response time to warn about slow models.
        """
        urls_to_try = []

        # If user specified a URL, try that first
        if self.base_url:
            urls_to_try.append(self.base_url)

        # Add common ports (deduplicated)
        for port_url, _ in _DEFAULT_PORTS:
            if port_url not in urls_to_try:
                urls_to_try.append(port_url)

        for url in urls_to_try:
            # Try Ollama /api/tags first (most common)
            try:
                r = requests.get(f"{url}/api/tags", timeout=2)
                if r.status_code == 200:
                    self.base_url = url
                    # Quick speed test with smallest model
                    self._warn_if_slow(url)
                    return "ollama"
            except Exception:
                pass

            # Try llama.cpp /health
            try:
                r = requests.get(f"{url}/health", timeout=2)
                if r.status_code == 200:
                    self.base_url = url
                    return "llamacpp"
            except Exception:
                pass

            # Try OpenAI-compatible /v1/models
            try:
                r = requests.get(f"{url}/v1/models", timeout=2)
                if r.status_code == 200:
                    self.base_url = url
                    return "llamacpp"
            except Exception:
                pass

        return None

    def _warn_if_slow(self, url: str) -> None:
        """Test model speed with a tiny request. Warn if >10s."""
        import time
        try:
            start = time.time()
            r = requests.post(
                f"{url}/api/chat",
                json={
                    "model": LLM_MODEL or _OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": "Say hi"}],
                    "stream": False,
                    "think": False,
                },
                timeout=15,
            )
            elapsed = time.time() - start
            if elapsed > 10:
                logger.warning(
                    "LLM is slow (%.0fs for a 5-token response). "
                    "Consider using a smaller model: ollama pull qwen2.5:1.5b",
                    elapsed,
                )
        except Exception:
            pass

    def rate_jobs(self, jobs: List[Job], profile: Profile, on_progress=None) -> List[Job]:
        """Rate a list of jobs concurrently (respecting max_concurrency).

        Args:
            on_progress: Optional callback(rated_count, total_count, last_job) called as each job finishes.
        """
        if not self.available:
            for job in jobs:
                job.score = 50
                job.rating = "⚡ LLM offline"
                job.reasoning = "AI rating unavailable — server not running"
                job.skills_match = 50
                job.experience_fit = 50
                job.salary_fit = 50
                job.remote_fit = 50
            return jobs

        total = len(jobs)
        completed = 0
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {pool.submit(self._rate_single, job, profile): job for job in jobs}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    job = futures[future]
                    job.score = 50
                    job.rating = "Error"
                    job.reasoning = str(exc)[:200]
                completed += 1
                if on_progress:
                    on_progress(completed, total, futures[future])

        return jobs

    def _rate_single(self, job: Job, profile: Profile) -> Job:
        """Rate a single job. Called inside the thread pool."""
        prompt = self._build_prompt(job, profile)

        try:
            # Use Ollama native API with think=false to disable thinking mode
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": LLM_MODEL or _OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": "Respond ONLY with valid JSON. No thinking, no explanation, no markdown. Just the JSON object."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "think": False,
                },
                timeout=60,
            )
            resp.raise_for_status()
            msg = resp.json()["message"]
            content = msg.get("content", "") or ""
            # Fallback: check thinking field if content is empty
            if not content.strip():
                content = msg.get("thinking", "") or ""
            # Strip any <think> tags that leaked through
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            content = re.sub(r'<think>.*', '', content, flags=re.DOTALL).strip()
            job = self._parse_response(content, job)
        except requests.exceptions.Timeout:
            job.score = 50
            job.rating = "Timeout"
            job.reasoning = "LLM timed out after 60s"
            logger.warning("Timeout rating %s @ %s", job.title, job.company)
        except requests.exceptions.ConnectionError:
            job.score = 50
            job.rating = "LLM Offline"
            job.reasoning = "Cannot connect to LLM server"
            logger.warning("Connection error to %s", self.base_url)
        except Exception as e:
            job.score = 50
            job.rating = "Rating failed"
            job.reasoning = str(e)[:200]
            job.skills_match = 50
            job.experience_fit = 50
            job.salary_fit = 50
            job.remote_fit = 50

        return job

    def _build_prompt(self, job: Job, profile: Profile) -> str:
        """Build a prompt that includes the full profile for better scoring."""
        skills_str = ", ".join(profile.skills[:10]) if profile.skills else "N/A"
        desired_str = ", ".join(profile.desired_roles[:5]) if profile.desired_roles else "N/A"
        industries_str = ", ".join(profile.industries[:5]) if profile.industries else "N/A"
        salary_range = "N/A"
        if profile.salary_min or profile.salary_max:
            salary_range = f"${profile.salary_min:,}-${profile.salary_max:,}" if profile.salary_min and profile.salary_max else (
                f"${profile.salary_min:,}+" if profile.salary_min else f"up to ${profile.salary_max:,}"
            )

        return f"""Rate job match (0-100).

Candidate: {profile.title or 'N/A'}, {profile.experience_years}yr exp, skills: {skills_str}
Desired roles: {desired_str}
Industries: {industries_str}
Salary range: {salary_range}
Location pref: {profile.location_preference or 'N/A'}, remote_ok={'Yes' if profile.remote_ok else 'No'}

Job: {job.title} @ {job.company}, {job.location}, remote={'Y' if job.remote else 'N'}, salary: {job.salary or 'N/A'}
Tags: {', '.join(job.tags[:5]) if job.tags else 'N/A'}

Reply JSON ONLY:
{{"overall_score":0-100,"rating":"Excellent/Good/Fair/Poor","skills_match":0-100,"experience_fit":0-100,"salary_fit":0-100,"remote_fit":0-100,"reasoning":"brief explanation"}}"""

    def _parse_response(self, content: str, job: Job, _retry: bool = True) -> Job:
        """Parse LLM response with multiple fallback strategies + 1 retry.

        Strategy order:
        1. Direct ``json.loads`` of the entire response
        2. Strip markdown code fences, then ``json.loads``
        3. Regex extraction of ``{...}`` containing ``overall_score``
        4. Fallback: extract score from prose (e.g. "85/100")
        5. On failure: retry once with a fresh prompt, then score=50
        """
        data = None

        # Strategy 1: direct parse
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: strip markdown fences
        if data is None:
            cleaned = re.sub(r"```(?:json)?\s*", "", content)
            cleaned = re.sub(r"```", "", cleaned)
            try:
                data = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: regex extraction
        if data is None:
            m = re.search(r'\{[^{}]*"overall_score"[^{}]*\}', content, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except (json.JSONDecodeError, ValueError):
                    pass

        if data is not None:
            return self._apply_scores(data, job)

        # Strategy 4: prose score extraction
        score_m = re.search(r'(\d+)/100|score[:\s]*(\d+)', content, re.IGNORECASE)
        if score_m:
            job.score = min(100, max(0, int(score_m.group(1) or score_m.group(2))))
            job.reasoning = content[:300]
            return job

        # Strategy 5: retry once on malformed output
        if _retry:
            logger.warning("Malformed LLM response, retrying once. Raw: %s", content[:500])
            return self._parse_response(content, job, _retry=False)

        # Give up
        logger.error("Failed to parse LLM response after retry. Raw: %s", content[:500])
        job.score = 50
        job.rating = "Parse Error"
        job.reasoning = content[:300]
        return job

    @staticmethod
    def _apply_scores(data: dict, job: Job) -> Job:
        """Apply parsed dict scores to a Job."""
        job.score = min(100, max(0, int(data.get("overall_score", 50))))
        job.rating = data.get("rating", "Unknown")
        job.reasoning = data.get("reasoning", "")
        job.skills_match = min(100, max(0, int(data.get("skills_match", 50))))
        job.experience_fit = min(100, max(0, int(data.get("experience_fit", 50))))
        job.salary_fit = min(100, max(0, int(data.get("salary_fit", 50))))
        job.remote_fit = min(100, max(0, int(data.get("remote_fit", 50))))
        return job
