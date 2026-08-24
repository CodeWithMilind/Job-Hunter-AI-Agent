"""Unified Hirevia job-search pipeline used by both the CLI and dashboard."""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from hirevia.cache import SeenJobsCache
from hirevia.display import console, display_header, display_jobs, export_results
from hirevia.eligibility import INDIA_ELIGIBLE
from hirevia.models import Job, Profile
from hirevia.rating import AIRater
from hirevia.quality import (
    LinkState,
    duplicate_key,
    freshness_score,
    is_expired,
    is_fresher_eligible,
    is_target_role,
    is_likely_application_url,
    is_relevant,
    profile_match_score,
    profile_matches,
    relevance_score,
    verify_application_link,
    role_match,
)
from hirevia.llm_relevance import LLMRelevance
from hirevia.nvidia_client import NVIDIAClient
from hirevia.sources import SourceRegistry

DEFAULT_PROFILE = "profile.yaml"
DEFAULT_COMPANIES = "companies.yaml"
DEFAULT_SOURCES = "sources.yaml"

logger = logging.getLogger(__name__)


def deduplicate_jobs(jobs: List[Job]) -> List[Job]:
    """Deduplicate all jobs by stable URL/ID, then company/title/location fallback."""
    seen = set()
    fallback_indexes: Dict[str, int] = {}
    unique: List[Job] = []

    def directness(job: Job) -> int:
        url = (job.url or "").lower()
        return 0 if "t.me/" in url or "telegram.me/" in url else 1

    for job in jobs:
        key = duplicate_key(job)
        fallback = "|".join(
            value.lower().strip()
            for value in (job.company, job.title, job.location)
        )
        has_reliable_identity = key.startswith("url:") or key.startswith("id:")
        if has_reliable_identity and urlsplit(job.url).netloc in {"example.com", "example.org", "example.net"}:
            has_reliable_identity = False
        if key not in seen and fallback not in fallback_indexes:
            seen.add(key)
            fallback_indexes[fallback] = len(unique)
            unique.append(job)
        elif fallback in fallback_indexes:
            index = fallback_indexes[fallback]
            current = unique[index]
            if directness(job) > directness(current):
                unique[index] = job
    return unique


def normalize_job(job: Job) -> Job:
    job.title = (job.title or "").strip() or "Unknown"
    job.company = (job.company or "").strip() or "Unknown"
    job.location = (job.location or "").strip() or "Remote"
    job.url = job.url or job.original_url or ""
    if not job.source:
        job.source = "Unknown"
    return job


def serialize_job(job: Job) -> Dict[str, Any]:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "source": job.source,
        "source_type": job.source_type,
        "url": job.url,
        "description": job.description,
        "salary": job.salary,
        "remote": job.remote,
        "tags": job.tags,
        "posted": job.posted,
        "score": job.score,
        "rating": job.rating,
        "reasoning": job.reasoning,
        "skills_match": getattr(job, "skills_match", 0),
        "experience_fit": getattr(job, "experience_fit", 0),
        "salary_fit": getattr(job, "salary_fit", 0),
        "remote_fit": getattr(job, "remote_fit", 0),
        "india_eligibility": job.india_eligibility,
        "source_metadata": job.source_metadata,
    }


def search_jobs(
    query: str,
    location: str = "",
    profile: Optional[Profile] = None,
    ai_enabled: bool = True,
    export_path: str = "",
    limit: int = 50,
    max_pages: int = 3,
    max_concurrency: int = 3,
    enable_linkedin: bool = False,
    companies_path: str = DEFAULT_COMPANIES,
    sources_path: str = DEFAULT_SOURCES,
    cache_days: int = 7,
    no_cache: bool = False,
    india_eligible_only: bool = False,
    llm_url: str = "",
    llm_model: str = "",
    show_output: bool = True,
    source_ids: Optional[List[str]] = None,
    scan_stats: Optional[Dict[str, Any]] = None,
) -> List[Job]:
    ai_enabled = ai_enabled and os.environ.get("LLM_ENABLED", "true").lower() not in {"0", "false", "no"}
    """Execute the shared search pipeline used by the CLI and API."""
    if show_output:
        display_header()
        console.print(f"\n[bold cyan]🔍 Searching for:[/bold cyan] [bold white]{query}[/bold white]")
        if location:
            console.print(f"[bold cyan]📍 Location:[/bold cyan] [bold white]{location}[/bold white]")
        console.print()

    # Create the existing local AI client once. No AI mode never initializes
    # or calls any LLM component.
    nvidia_client = NVIDIAClient() if ai_enabled else None
    use_nvidia = bool(nvidia_client and nvidia_client.available)
    if use_nvidia:
        logger.info("NVIDIA provider: enabled")
        logger.info("Query model: %s", nvidia_client.query_model)
        logger.info("Job model: %s", nvidia_client.job_model)
        logger.info("Ranking model: %s", nvidia_client.ranking_model)
    ai_rater = AIRater(base_url=llm_url, max_concurrency=max_concurrency) if ai_enabled and not use_nvidia else None
    semantic = None
    intent = None
    nvidia_intent = None
    if use_nvidia:
        nvidia_intent = nvidia_client.analyze_query(query, location)
    elif ai_rater is not None and ai_rater.available:
        semantic = LLMRelevance(base_url=getattr(ai_rater, "base_url", ""), model=llm_model)
        intent = semantic.understand_query(query, location)

    registry = SourceRegistry.from_yaml(sources_path)
    sources = registry.enabled_sources(overrides={"linkedin": True} if enable_linkedin else None)
    if source_ids is not None:
        sources = [source for source in sources if source.source_id in source_ids]

    all_jobs: List[Job] = []
    source_counts: Dict[str, int] = {}
    source_errors: Dict[str, str] = {}
    stats = scan_stats if scan_stats is not None else {}
    stats.update({"sources": {}, "raw_jobs": 0, "normalized": 0, "processed_jobs": []})
    logger.info("[SCAN] Started (%d sources)", len(sources))
    profile = profile or Profile(name="Job Seeker")

    if show_output:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

        progress_context = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        )
    else:
        progress_context = None

    if progress_context:
        progress_context.__enter__()
        task = progress_context.add_task("Searching sources...", total=len(sources))
    else:
        task = None

    try:
        if not sources:
            logger.warning("[SCAN] No enabled sources configured")
        with ThreadPoolExecutor(max_workers=max(1, min(len(sources), 10))) as pool:
            future_to_source = {}
            for source in sources:
                logger.info("[SOURCE] %s: fetching", source.name)
                future_to_source[pool.submit(
                    source.fetch_safely,
                    query,
                    location=location,
                    limit=limit,
                    max_pages=max_pages,
                    companies_path=companies_path,
                )] = source

            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    result = future.result()
                    if result.error:
                        raise RuntimeError(result.error)
                    source_counts[source.name] = len(result.jobs)
                    stats["sources"][source.source_id] = {"name": source.name, "type": source.source_type, "jobs": len(result.jobs), "error": ""}
                    telegram_stats = getattr(source, "last_scan_stats", None)
                    if telegram_stats:
                        stats["telegram"] = dict(telegram_stats)
                    logger.info("[SOURCE] %s: %d jobs", source.name, len(result.jobs))
                    for job in result.jobs:
                        normalize_job(job)
                    all_jobs.extend(result.jobs)
                    stats["normalized"] = len(all_jobs)
                    if task is not None:
                        progress_context.update(
                            task,
                            advance=1,
                            description=f"[green]{source.name}: {len(result.jobs)} jobs[/green]",
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("%s search failed: %s", source.name, exc)
                    source_counts[source.name] = 0
                    source_errors[source.name] = str(exc)
                    stats["sources"][source.source_id] = {"name": source.name, "type": source.source_type, "jobs": 0, "error": str(exc)}
                    logger.error("[SOURCE] %s: ERROR %s", source.name, exc)
                    if task is not None:
                        progress_context.update(
                            task,
                            advance=1,
                            description=f"[red]{source.name}: error[/red]",
                        )
    finally:
        if progress_context is not None:
            progress_context.__exit__(None, None, None)

    stats["raw_jobs"] = len(all_jobs)
    logger.info("[SCAN] Raw jobs: %d", stats["raw_jobs"])
    logger.info("[SCAN] Normalized: %d", stats["normalized"])
    deduplicated_jobs = deduplicate_jobs(all_jobs)
    stats["after_deduplication"] = len(deduplicated_jobs)
    # Quality metadata is attached to every discovered job. Discovery is not
    # a match decision, so weak or incomplete records remain processable.
    quality_jobs: List[Job] = []
    link_states: Dict[str, LinkState] = {}
    for job in deduplicated_jobs:
        if is_expired(job):
            job.source_metadata = {**(job.source_metadata or {}), "match_excluded": "expired"}
        if job.url and job.source != "Telegram" and is_likely_application_url(job.url):
            link_key = job.url.lower().strip()
            link_state = link_states.get(link_key)
            if link_state is None:
                # Keep live verification bounded; unknown network failures do
                # not reject the opportunity.
                if len(link_states) >= 12:
                    link_state = LinkState.UNKNOWN
                else:
                    link_state = verify_application_link(job.url, timeout=1, retries=0)
                link_states[link_key] = link_state
            job.source_metadata = {
                **(job.source_metadata or {}),
                "application_link_state": link_state.value,
            }
            if link_state == LinkState.VERIFIED_UNAVAILABLE:
                job.source_metadata = {**(job.source_metadata or {}), "match_excluded": "unavailable_link"}
        job.source_metadata = {
            **(job.source_metadata or {}),
            "freshness_score": freshness_score(job),
        }
        quality_jobs.append(job)
    all_jobs = quality_jobs
    stats["processed_jobs"] = list(all_jobs)
    stats["india_eligible"] = sum(job.india_eligibility == INDIA_ELIGIBLE for job in deduplicated_jobs)
    stats["fresher_eligible"] = sum(is_fresher_eligible(job) for job in deduplicated_jobs if job.india_eligibility == INDIA_ELIGIBLE)
    stats["relevant_roles"] = sum(is_target_role(job) for job in quality_jobs)
    logger.info("[SCAN] Deduplicated: %d", stats["after_deduplication"])
    logger.info("[SCAN] Processed before matching: %d", len(all_jobs))

    cache = None
    if not no_cache:
        try:
            cache = SeenJobsCache(ttl_days=cache_days)
            before_count = len(all_jobs)
            new_jobs = cache.filter_new(all_jobs)
            stats["new"] = len(new_jobs)
            if show_output and before_count != len(all_jobs):
                console.print(
                    f"[dim]📋 Cache: {before_count} → {len(new_jobs)} new jobs ({before_count - len(new_jobs)} previously seen, {cache_days}d TTL)[/dim]"
                )
        except Exception as exc:
            logger.warning("Cache unavailable: %s", exc)

    # India-only is a product invariant; retain the old parameter for API
    # compatibility but never allow it to be disabled.
    if show_output:
        src_summary = ", ".join(f"{name}: {count}" for name, count in source_counts.items() if count > 0)
        console.print(
            f"\n[green]✓ Found {len(all_jobs)} unique jobs from {len([count for count in source_counts.values() if count > 0])} sources ({src_summary})[/green]\n"
        )

    if not all_jobs:
        if cache is not None:
            cache.close()
        if show_output:
            console.print("[bold red]No jobs found.[/bold red]")
        return []

    if ai_enabled and not use_nvidia:
        if llm_model:
            import hirevia.rating as rating_module
            rating_module.LLM_MODEL = llm_model

        rater = ai_rater or AIRater(base_url=llm_url, max_concurrency=max_concurrency)
        if rater.available:
            if show_output:
                import hirevia.rating as rating_module
                console.print(f"[bold cyan]🤖 Rating jobs with local LLM ({rating_module.LLM_MODEL}) (concurrency={max_concurrency})...[/bold cyan]\n")

            start_time = time.time()
            if show_output:
                from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

                progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("{task.completed}/{task.total}"),
                    TextColumn("[dim]{task.fields[elapsed]}[/dim]"),
                    console=console,
                )
                progress.__enter__()
                task = progress.add_task("AI Rating...", total=len(all_jobs), elapsed="")

                def _on_progress(done: int, total: int, job: Job):
                    elapsed = time.strftime("%M:%S", time.gmtime(time.time() - start_time))
                    desc = f"AI Rating... [green]{job.score}[/green] {job.company}"
                    progress.update(task, completed=done, description=desc, elapsed=elapsed)

                try:
                    rater.rate_jobs(all_jobs, profile, on_progress=_on_progress)
                    progress.update(task, completed=len(all_jobs), elapsed=time.strftime("%M:%S", time.gmtime(time.time() - start_time)))
                finally:
                    progress.__exit__(None, None, None)
            else:
                rater.rate_jobs(all_jobs, profile)
        else:
            if show_output:
                console.print("[yellow]⚠ Local LLM not available. Running without AI ratings.[/yellow]")
                console.print("[dim]  Auto-detected ports: 11434 (Ollama), 8080 (llama.cpp), 1234 (LM Studio)[/dim]")
                console.print("[dim]  Start Ollama, llama-server, or LM Studio, then try again[/dim]\n")
            pass
        for job in all_jobs:
            profile_score = profile_match_score(job, profile)
            if not (getattr(profile, "target_roles", []) or getattr(profile, "desired_roles", []) or getattr(profile, "keywords", []) or getattr(profile, "skills", [])):
                profile_score = relevance_score(query, job)
            freshness = int((job.source_metadata or {}).get("freshness_score", 25))
            job.score = round(profile_score * 0.65 + relevance_score(query, job) * 0.10 + freshness * 0.20 + (5 if job.source == "Greenhouse" else 0))
            job.rating = "Deterministic"

    if use_nvidia and nvidia_intent is not None:
        candidate_limit = nvidia_client.max_candidates
        candidates = sorted(all_jobs, key=lambda item: relevance_score(query, item), reverse=True)[:candidate_limit]
        analyses = nvidia_client.analyze_jobs(query, nvidia_intent, candidates)
        filtered_jobs: List[Job] = []
        for job in all_jobs:
            identity = nvidia_client.job_identity(job)
            analysis = analyses.get(identity)
            if analysis is None:
                # A failed model call falls back to deterministic relevance.
                filtered_jobs.append(job)
                continue
            job.source_metadata = {
                **(job.source_metadata or {}),
                "nvidia_relevance": analysis.relevance_score,
                "nvidia_role_match": analysis.role_match,
                "nvidia_skill_match": analysis.skill_match,
                "nvidia_technology_match": analysis.technology_match,
                "nvidia_seniority_match": analysis.seniority_match,
                "nvidia_matched_requirements": analysis.matched_requirements,
                "nvidia_missing_requirements": analysis.missing_requirements,
                "nvidia_reasons": analysis.reasons,
            }
            if not analysis.hard_mismatch and analysis.relevant and analysis.relevance_score >= 60:
                filtered_jobs.append(job)
        for job in all_jobs:
            if job not in filtered_jobs:
                job.source_metadata = {**(job.source_metadata or {}), "llm_match": False}
        # Keep the complete processed set for persistence; semantic results
        # influence matching below rather than deleting scanned records.
        ranked = nvidia_client.rank_jobs(query, nvidia_intent, all_jobs, analyses)
        for job in all_jobs:
            ranking = ranked.get(nvidia_client.job_identity(job))
            if ranking:
                job.source_metadata["nvidia_final_score"] = ranking["final_score"]
                job.source_metadata["nvidia_ranking_reason"] = ranking["reason"]
    elif semantic is not None and intent is not None:
        candidate_limit = max(1, int(os.environ.get("LLM_CANDIDATE_LIMIT", "30")))
        candidates = sorted(
            all_jobs,
            key=lambda item: relevance_score(query, item),
            reverse=True,
        )[:candidate_limit]
        semantic_results = semantic.evaluate_jobs(query, intent, candidates, limit=candidate_limit)
        filtered_jobs: List[Job] = []
        for job in all_jobs:
            deterministic = relevance_score(query, job)
            job.source_metadata = {
                **(job.source_metadata or {}),
                "deterministic_relevance": deterministic,
            }
            result = semantic_results.get(semantic.job_key(query, job)[1])
            if result is not None:
                job.source_metadata.update({
                    "llm_relevance": result.score,
                    "llm_relevance_reason": result.reason,
                    "llm_matched_skills": result.matched_skills,
                    "llm_missing_skills": result.missing_skills,
                    "llm_confidence": result.confidence,
                })
                strong_deterministic = deterministic >= 85
                if not ((result.relevant and result.score >= 70) or (strong_deterministic and result.score >= 50)):
                    job.source_metadata["llm_match"] = False
        # Do not discard scanned jobs when semantic matching rejects them.

    for job in all_jobs:
        metadata = job.source_metadata or {}
        if not ai_enabled:
            job.score = profile_match_score(job, profile)
            job.rating = "Deterministic"
            if metadata.get("match_excluded"):
                job.score = 0
                job.rating = "Scanned only"
            continue
        freshness = int(metadata.get("freshness_score", 25))
        deterministic = int(metadata.get("deterministic_relevance", relevance_score(query, job)))
        nvidia_score = metadata.get("nvidia_final_score", metadata.get("nvidia_relevance"))
        if nvidia_score is not None:
            application_quality = 100 if metadata.get("application_link_state") == "verified_active" else 60
            job.score = round(
                float(nvidia_score) * 0.40
                + deterministic * 0.25
                + freshness * 0.20
                + application_quality * 0.10
                + job.score * 0.05
            )
            job.reasoning = metadata.get("nvidia_ranking_reason") or "; ".join(metadata.get("nvidia_reasons", []))
            job.rating = "NVIDIA AI"
            if metadata.get("llm_match") is False and metadata.get("llm_relevance") is not None:
                job.score = 0
            continue
        llm_score = metadata.get("llm_relevance")
        if llm_score is not None:
            application_quality = 100 if metadata.get("application_link_state") == "verified_active" else 60
            job.score = round(
                float(llm_score) * 0.40
                + deterministic * 0.25
                + freshness * 0.20
                + application_quality * 0.10
                + job.score * 0.05
            )
        else:
            job.score = round((job.score * 0.8) + (freshness * 0.2))
        if metadata.get("llm_match") is False and metadata.get("llm_relevance") is not None:
            job.score = 0
        if metadata.get("match_excluded"):
            job.score = 0
            job.rating = "Scanned only"
    all_jobs.sort(key=lambda job: job.score, reverse=True)
    stats["matched"] = sum(job.score >= 50 for job in all_jobs)
    stats["stored_scanned"] = len(all_jobs)
    matched_jobs = [job for job in all_jobs if job.score >= 50]
    stats["final_jobs"] = len(matched_jobs)
    logger.info("[SCAN] Scored: %d", stats["stored_scanned"])
    logger.info("[SCAN] Stored/Scanned: %d", stats["stored_scanned"])
    logger.info("[SCAN] Matched: %d", stats["matched"])
    logger.info("[SCAN] New: %d", stats.get("new", stats["stored_scanned"]))

    if show_output:
        display_jobs(matched_jobs, profile, ai_enabled)

    if export_path:
        fmt = "csv" if export_path.endswith(".csv") else "json"
        export_results(matched_jobs, export_path, fmt)
    elif show_output:
        auto_path = os.path.join(os.getcwd(), "results.csv")
        export_results(all_jobs, auto_path, "csv")

    if cache is not None:
        try:
            cache.mark_seen(all_jobs)
            if show_output:
                stats = cache.stats()
                console.print(f"[dim]📋 Cache: {stats['active_entries']} active entries ({stats['ttl_days']}d TTL)[/dim]")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Cache update failed: %s", exc)
        finally:
            cache.close()

    return matched_jobs
