"""Unified Hirevia job-search pipeline used by both the CLI and dashboard."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from hirevia.cache import SeenJobsCache
from hirevia.display import console, display_header, display_jobs, export_results
from hirevia.eligibility import INDIA_ELIGIBLE
from hirevia.models import Job, Profile
from hirevia.rating import AIRater
from hirevia.sources import SourceRegistry

DEFAULT_PROFILE = "profile.yaml"
DEFAULT_COMPANIES = "companies.yaml"
DEFAULT_SOURCES = "sources.yaml"

logger = logging.getLogger(__name__)


def deduplicate_jobs(jobs: List[Job]) -> List[Job]:
    """Deduplicate normal jobs by title/company and Telegram by stable identity."""
    seen = set()
    seen_telegram = set()
    unique: List[Job] = []
    for job in jobs:
        if job.source == "Telegram":
            metadata = job.source_metadata or {}
            identity = (
                metadata.get("telegram_message_url")
                or job.url
                or metadata.get("telegram_message_id")
                or f"{job.title}|{job.company}"
            ).lower().strip()
            if identity not in seen_telegram:
                seen_telegram.add(identity)
                unique.append(job)
            continue
        key = (job.title.lower().strip(), job.company.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(job)
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
) -> List[Job]:
    """Execute the shared search pipeline used by the CLI and API."""
    if show_output:
        display_header()
        console.print(f"\n[bold cyan]🔍 Searching for:[/bold cyan] [bold white]{query}[/bold white]")
        if location:
            console.print(f"[bold cyan]📍 Location:[/bold cyan] [bold white]{location}[/bold white]")
        console.print()

    registry = SourceRegistry.from_yaml(sources_path)
    sources = registry.enabled_sources(overrides={"linkedin": True} if enable_linkedin else None)

    all_jobs: List[Job] = []
    source_counts: Dict[str, int] = {}

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
        with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as pool:
            future_to_source = {
                pool.submit(
                    source.fetch_safely,
                    query,
                    location=location,
                    limit=limit,
                    max_pages=max_pages,
                    companies_path=companies_path,
                ): source
                for source in sources
            }

            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    result = future.result()
                    if result.error:
                        raise RuntimeError(result.error)
                    source_counts[source.name] = len(result.jobs)
                    for job in result.jobs:
                        normalize_job(job)
                    all_jobs.extend(result.jobs)
                    if task is not None:
                        progress_context.update(
                            task,
                            advance=1,
                            description=f"[green]{source.name}: {len(result.jobs)} jobs[/green]",
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("%s search failed: %s", source.name, exc)
                    source_counts[source.name] = 0
                    if task is not None:
                        progress_context.update(
                            task,
                            advance=1,
                            description=f"[red]{source.name}: error[/red]",
                        )
    finally:
        if progress_context is not None:
            progress_context.__exit__(None, None, None)

    all_jobs = deduplicate_jobs(all_jobs)

    cache = None
    if not no_cache:
        try:
            cache = SeenJobsCache(ttl_days=cache_days)
            before_count = len(all_jobs)
            all_jobs = cache.filter_new(all_jobs)
            if show_output and before_count != len(all_jobs):
                console.print(
                    f"[dim]📋 Cache: {before_count} → {len(all_jobs)} new jobs ({before_count - len(all_jobs)} previously seen, {cache_days}d TTL)[/dim]"
                )
        except Exception as exc:
            logger.warning("Cache unavailable: %s", exc)

    if india_eligible_only:
        all_jobs = [job for job in all_jobs if job.india_eligibility == INDIA_ELIGIBLE]

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

    profile = profile or Profile(name="Job Seeker")

    if ai_enabled:
        if llm_model:
            import hirevia.rating as rating_module
            rating_module.LLM_MODEL = llm_model

        rater = AIRater(base_url=llm_url, max_concurrency=max_concurrency)
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
            for job in all_jobs:
                job.score = 50
                job.rating = "No AI"
    else:
        for job in all_jobs:
            job.score = 50
            job.rating = "Skipped"

    all_jobs.sort(key=lambda job: job.score, reverse=True)

    if show_output:
        display_jobs(all_jobs, profile, ai_enabled)

    if export_path:
        fmt = "csv" if export_path.endswith(".csv") else "json"
        export_results(all_jobs, export_path, fmt)
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

    return all_jobs
