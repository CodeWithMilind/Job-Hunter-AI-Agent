#!/usr/bin/env python3
"""
🎯 JobRadar — AI-Powered Job Search Agent
CLI entrypoint.  Invoke as ``python -m jobradar`` or ``python -m jobradar.cli``.
"""

import argparse
import logging
import os
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from jobradar.models import Job, Profile
from jobradar.eligibility import INDIA_ELIGIBLE
from jobradar.display import console, display_header, display_jobs, export_results
from jobradar.rating import AIRater, LLM_URL
from jobradar.cache import SeenJobsCache
from jobradar.sources import SourceRegistry

DEFAULT_PROFILE = "profile.yaml"
DEFAULT_COMPANIES = "companies.yaml"
DEFAULT_SOURCES = "sources.yaml"

logger = logging.getLogger(__name__)


# ─── Search pipeline ───────────────────────────────────────────────────────

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
) -> List[Job]:
    """Main search pipeline with concurrent source queries."""
    display_header()

    # Step 1: Search multiple sources concurrently
    console.print(f"\n[bold cyan]🔍 Searching for:[/bold cyan] [bold white]{escape(query)}[/bold white]")
    if location:
        console.print(f"[bold cyan]📍 Location:[/bold cyan] [bold white]{escape(location)}[/bold white]")
    console.print()

    registry = SourceRegistry.from_yaml(sources_path)
    sources = registry.enabled_sources(overrides={"linkedin": True} if enable_linkedin else None)

    all_jobs: List[Job] = []
    source_counts = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Searching sources...", total=len(sources))

        with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as pool:
            future_to_source = {
                pool.submit(source.fetch_safely, query, location=location, limit=limit, max_pages=max_pages, companies_path=companies_path): source
                for source in sources
            }
            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    result = future.result()
                    if result.error:
                        raise RuntimeError(result.error)
                    source_counts[source.name] = len(result.jobs)
                    all_jobs.extend(result.jobs)
                    progress.update(
                        task,
                        advance=1,
                        description=f"[green]{source.name}: {len(result.jobs)} jobs[/green]",
                    )
                except Exception as e:
                    logger.warning("%s search failed: %s", source.name, e)
                    source_counts[source.name] = 0
                    progress.update(
                        task,
                        advance=1,
                        description=f"[red]{source.name}: error[/red]",
                    )

    # Deduplicate by title + company
    seen = set()
    unique_jobs: List[Job] = []
    for j in all_jobs:
        key = (j.title.lower().strip(), j.company.lower().strip())
        if key not in seen:
            seen.add(key)
            unique_jobs.append(j)
    all_jobs = unique_jobs

    if india_eligible_only:
        all_jobs = [job for job in all_jobs if job.india_eligibility == INDIA_ELIGIBLE]
        console.print(f"[green]✓ India eligible: {len(all_jobs)} jobs[/green]")

    # Apply persistent seen-jobs cache
    cache = None
    before_count = len(all_jobs)
    if not no_cache:
        try:
            cache = SeenJobsCache(ttl_days=cache_days)
            all_jobs = cache.filter_new(all_jobs)
            if before_count != len(all_jobs):
                console.print(f"[dim]📋 Cache: {before_count} → {len(all_jobs)} new jobs ({before_count - len(all_jobs)} previously seen, {cache_days}d TTL)[/dim]")
        except Exception as e:
            logger.warning("Cache unavailable: %s", e)

    src_summary = ", ".join(f"{n}: {c}" for n, c in source_counts.items() if c > 0)
    console.print(f"\n[green]✓ Found {len(all_jobs)} unique jobs from {len([c for c in source_counts.values() if c > 0])} sources ({src_summary})[/green]\n")

    if not all_jobs:
        if cache:
            cache.close()
        return []

    # Step 2: AI Rating
    if ai_enabled:
        # Set model before creating rater
        if llm_model:
            import jobradar.rating as _rating
            _rating.LLM_MODEL = llm_model
        rater = AIRater(base_url=llm_url, max_concurrency=max_concurrency)
        if rater.available:
            import jobradar.rating as _rating
            console.print(f"[bold cyan]🤖 Rating jobs with local LLM ({_rating.LLM_MODEL}) (concurrency={max_concurrency})...[/bold cyan]\n")
            profile = profile or Profile(name="Job Seeker")

            start_time = time.time()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TextColumn("[dim]{task.fields[elapsed]}[/dim]"),
                console=console,
            ) as progress:
                task = progress.add_task("AI Rating...", total=len(all_jobs), elapsed="")

                def _on_progress(done, total, job):
                    elapsed = time.strftime("%M:%S", time.gmtime(time.time() - start_time))
                    desc = f"AI Rating... [green]{job.score}[/green] {job.company}"
                    progress.update(task, completed=done, description=desc, elapsed=elapsed)

                rater.rate_jobs(all_jobs, profile, on_progress=_on_progress)
                elapsed = time.strftime("%M:%S", time.gmtime(time.time() - start_time))
                progress.update(task, completed=len(all_jobs), elapsed=elapsed)
        else:
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

    # Step 3: Display
    display_jobs(all_jobs, profile, ai_enabled)

    # Step 4: Export
    if export_path:
        fmt = "csv" if export_path.endswith(".csv") else "json"
        export_results(all_jobs, export_path, fmt)
    else:
        # Auto-save to results.csv in current directory
        auto_path = os.path.join(os.getcwd(), "results.csv")
        export_results(all_jobs, auto_path, "csv")

    # Step 5: Update cache with displayed jobs
    if cache:
        try:
            cache.mark_seen(all_jobs)
            stats = cache.stats()
            console.print(f"[dim]📋 Cache: {stats['active_entries']} active entries ({stats['ttl_days']}d TTL)[/dim]")
        except Exception as e:
            logger.warning("Cache update failed: %s", e)
        finally:
            cache.close()

    return all_jobs


# ─── Interactive mode ──────────────────────────────────────────────────────

def interactive_mode(profile: Optional[Profile] = None):
    """Run in interactive mode."""
    import jobradar.rating as _rating

    display_header()

    console.print("[bold cyan]Welcome to JobRadar![/bold cyan]")
    console.print("[dim]Type your search query, or 'quit' to exit.[/dim]\n")

    if profile:
        console.print(f"[green]✓ Profile loaded:[/green] {profile.name} — {profile.title}")
        if profile.skills:
            console.print(f"  Skills: {', '.join(profile.skills[:8])}")
        console.print()

    while True:
        try:
            query = console.input("[bold cyan]🔍 Search> [/bold cyan]").strip()
            if not query or query.lower() in ("quit", "exit", "q"):
                console.print("\n[dim]Goodbye! 👋[/dim]")
                break

            location = console.input("[dim]📍 Location (Enter to skip)> [/dim]").strip()

            search_jobs(
                query=query,
                location=location,
                profile=profile,
                ai_enabled=True,
                llm_url=LLM_URL,
                llm_model=_rating.LLM_MODEL or "qwen3:1.7b",
            )
            console.print()

        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye! 👋[/dim]")
            break


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="🎯 JobRadar — AI-Powered Job Search Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          %(prog)s                                    # Interactive mode
          %(prog)s search -q "python developer"      # Quick search
          %(prog)s search -q "ML engineer" -p my.yaml # With profile
          %(prog)s search -q "backend" --export jobs.json
          %(prog)s search -q "data engineer" --no-ai  # Skip AI rating
          %(prog)s search -q "python" --cache-days 30 # 30-day cache
          %(prog)s search -q "python" --no-cache      # Ignore cache
        """),
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default: interactive mode
    parser.add_argument("-q", "--query", help="Search query (if provided, runs search mode)")
    parser.add_argument("-l", "--location", default="", help="Location filter")
    parser.add_argument("-p", "--profile", default=DEFAULT_PROFILE, help="Profile YAML file")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI rating (faster)")
    parser.add_argument("--export", help="Export results to file (JSON or CSV)")
    parser.add_argument("--limit", type=int, default=50, help="Max jobs per source (default: 50)")
    parser.add_argument("--llm-url", default="", help="LLM server URL (auto-detects if empty)")
    parser.add_argument("--llm-model", default="", help="LLM model name (default: qwen3:1.7b)")
    # Existing flags
    parser.add_argument("--max-pages", type=int, default=3, help="Max pages per source (default: 3)")
    parser.add_argument("--max-concurrency", type=int, default=3, help="Max concurrent AI rating calls (default: 3)")
    parser.add_argument("--enable-linkedin", action="store_true",
                        help="Enable LinkedIn scraping (off by default — may violate ToS)")
    # New flags: ATS + cache
    parser.add_argument("--companies", default=DEFAULT_COMPANIES,
                        help="Companies YAML for Greenhouse/Ashby (default: companies.yaml)")
    parser.add_argument("--sources", default=DEFAULT_SOURCES,
                        help="Source registry YAML (default: sources.yaml)")
    parser.add_argument("--cache-days", type=int, default=7,
                        help="Days to remember seen jobs (default: 7)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable seen-jobs cache")
    parser.add_argument("--india-eligible-only", action="store_true",
                        help="Keep only jobs deterministically eligible from India")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear seen-jobs cache and exit")
    parser.add_argument("--list-ats-companies", action="store_true",
                        help="Show configured Greenhouse/Ashby company slugs")

    args = parser.parse_args()

    # Handle cache management commands
    if args.clear_cache:
        from jobradar.cache import SeenJobsCache
        cache = SeenJobsCache()
        cache.clear()
        console.print("[green]✓ Seen-jobs cache cleared.[/green]")
        return

    if args.list_ats_companies:
        import yaml
        try:
            with open(args.companies) as f:
                data = yaml.safe_load(f)
            gh = data.get("greenhouse", []) if isinstance(data, dict) else []
            ab = data.get("ashby", []) if isinstance(data, dict) else []
            console.print(f"[bold cyan]Greenhouse ({len(gh)}):[/bold cyan] {', '.join(gh)}")
            console.print(f"[bold cyan]Ashby ({len(ab)}):[/bold cyan] {', '.join(ab)}")
        except FileNotFoundError:
            console.print(f"[red]Companies file not found: {args.companies}[/red]")
        return

    # Load profile
    profile = None
    profile_path = Path(args.profile)
    if profile_path.exists():
        try:
            profile = Profile.from_yaml(str(profile_path))
            console.print(f"[green]✓ Profile loaded:[/green] {profile.name}")
        except Exception as e:
            console.print(f"[yellow]⚠ Could not load profile: {e}[/yellow]")

    if args.query:
        # Direct search mode
        search_jobs(
            query=args.query,
            location=args.location,
            profile=profile,
            ai_enabled=not args.no_ai,
            export_path=args.export or "",
            limit=args.limit,
            max_pages=args.max_pages,
            max_concurrency=args.max_concurrency,
            enable_linkedin=args.enable_linkedin,
            companies_path=args.companies,
            sources_path=args.sources,
            cache_days=args.cache_days,
            no_cache=args.no_cache,
            india_eligible_only=args.india_eligible_only,
            llm_url=args.llm_url,
            llm_model=args.llm_model,
        )
    else:
        # Interactive mode
        interactive_mode(profile)


if __name__ == "__main__":
    main()
