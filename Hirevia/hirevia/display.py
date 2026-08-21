"""Rich terminal display functions."""

import csv
import json
from dataclasses import asdict
from typing import List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hirevia.models import Job, Profile

console = Console()


def score_color(score: int) -> str:
    if score >= 80:
        return "bold green"
    elif score >= 60:
        return "bold yellow"
    elif score >= 40:
        return "bold dark_orange"
    else:
        return "bold red"


def score_bar(score: int) -> str:
    filled = score // 5
    empty = 20 - filled
    return f"[{score_color(score)}]{'█' * filled}[/{score_color(score)}][dim]{'░' * empty}[/dim] {score}/100"


def display_header():
    logo = """
[bold cyan]
  ╔═══════════════════════════════════════════════════╗
  ║   Hirevia                                         ║
  ║   AI-Powered Job & Opportunity Discovery          ║
  ╚═══════════════════════════════════════════════════╝[/bold cyan]"""
    console.print(logo)


def display_jobs(jobs: List[Job], profile: Optional[Profile] = None, ai_enabled: bool = True):
    if not jobs:
        console.print("\n[bold red]No jobs found.[/bold red] Try different keywords.\n")
        return

    # Sort by score (descending)
    jobs.sort(key=lambda j: j.score, reverse=True)

    # Main table
    table = Table(
        title=f"[bold cyan]Found {len(jobs)} Jobs[/bold cyan]",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Score", justify="center", width=12)
    table.add_column("Job Title", style="bold white", min_width=25, max_width=35)
    table.add_column("Company", style="cyan", min_width=15, max_width=20)
    table.add_column("Location", style="dim", min_width=12, max_width=18)
    table.add_column("Source", style="magenta", width=10)
    table.add_column("Salary", style="green", width=14)

    for i, job in enumerate(jobs, 1):
        score_text = score_bar(job.score) if ai_enabled else "[dim]N/A[/dim]"
        title_text = job.title[:33] + "…" if len(job.title) > 35 else job.title
        company_text = job.company[:18] + "…" if len(job.company) > 20 else job.company
        loc_text = job.location[:16] + "…" if len(job.location) > 18 else job.location
        salary_text = job.salary[:12] if job.salary else "[dim]—[/dim]"

        table.add_row(
            str(i),
            score_text,
            title_text,
            company_text,
            loc_text,
            f"[magenta]{job.source}[/magenta]",
            salary_text,
        )

    console.print(table)
    console.print()

    # Detailed view for top 5
    if ai_enabled:
        console.print("[bold cyan]━━━ Top 5 Match Details ━━━[/bold cyan]\n")
        for i, job in enumerate(jobs[:5], 1):
            rating_color = score_color(job.score)
            panel_content = Text()
            panel_content.append(f"  🔗 {job.url}\n\n", style="dim underline blue")
            panel_content.append(f"  Skills Match:    ", style="bold")
            panel_content.append(f"{job.skills_match}/100\n", style=score_color(job.skills_match))
            panel_content.append(f"  Experience Fit:  ", style="bold")
            panel_content.append(f"{job.experience_fit}/100\n", style=score_color(job.experience_fit))
            panel_content.append(f"  Salary Fit:      ", style="bold")
            panel_content.append(f"{job.salary_fit}/100\n", style=score_color(job.salary_fit))
            panel_content.append(f"  Remote Fit:      ", style="bold")
            panel_content.append(f"{job.remote_fit}/100\n", style=score_color(job.remote_fit))
            if job.reasoning:
                panel_content.append(f"\n  💡 {job.reasoning}\n", style="italic")

            console.print(Panel(
                panel_content,
                title=f"[bold {rating_color}]#{i} — {job.title} @ {job.company}  [{job.rating}]  Score: {job.score}/100[/bold {rating_color}]",
                border_style=rating_color,
                padding=(0, 1),
            ))


def export_results(jobs: List[Job], path: str, fmt: str = "json"):
    if fmt == "json":
        with open(path, "w") as f:
            json.dump([asdict(j) for j in jobs], f, indent=2, default=str)
    elif fmt == "csv":
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "title", "company", "location", "url", "salary",
                "source", "remote", "india_eligibility", "score", "rating", "reasoning",
            ])
            writer.writeheader()
            for j in jobs:
                writer.writerow({k: v for k, v in asdict(j).items() if k in writer.fieldnames})
    console.print(f"[green]✓ Exported {len(jobs)} jobs to {path}[/green]")
