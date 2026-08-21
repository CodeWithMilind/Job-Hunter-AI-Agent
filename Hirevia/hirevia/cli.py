#!/usr/bin/env python3
"""
Hirevia — AI-Powered Job & Opportunity Discovery
CLI entrypoint.  Invoke as ``python -m hirevia`` or ``python -m hirevia.cli``.
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

from rich.markup import escape

from hirevia.models import Job, Profile
from hirevia.pipeline import search_jobs as _search_jobs
from hirevia.display import console, display_header
from hirevia.rating import LLM_URL

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
    """Thin compatibility wrapper around the central shared pipeline."""
    return _search_jobs(
        query=query,
        location=location,
        profile=profile,
        ai_enabled=ai_enabled,
        export_path=export_path,
        limit=limit,
        max_pages=max_pages,
        max_concurrency=max_concurrency,
        enable_linkedin=enable_linkedin,
        companies_path=companies_path,
        sources_path=sources_path,
        cache_days=cache_days,
        no_cache=no_cache,
        india_eligible_only=india_eligible_only,
        llm_url=llm_url,
        llm_model=llm_model,
    )


# ─── Interactive mode ──────────────────────────────────────────────────────

def interactive_mode(profile: Optional[Profile] = None):
    """Run in interactive mode."""
    import hirevia.rating as _rating

    display_header()

    console.print("[bold cyan]Welcome to Hirevia![/bold cyan]")
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
        description="Hirevia — AI-Powered Job & Opportunity Discovery",
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
        from hirevia.cache import SeenJobsCache
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
