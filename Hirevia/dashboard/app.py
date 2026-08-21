"""Hirevia Dashboard — FastAPI backend."""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make sure hirevia package is importable
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hirevia-dashboard")

app = FastAPI(title="Hirevia Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic models ──────────────────────────────────────────────────────

class JobUpdate(BaseModel):
    status: Optional[str] = None

    url: Optional[str] = None

class SearchRequest(BaseModel):
    query: str
    location: str = ""
    limit: int = 50
    no_ai: bool = False

class ConfigUpdate(BaseModel):
    key: str
    value: str

class ConfigBulkUpdate(BaseModel):
    configs: Dict[str, str]

# ─── API Routes ────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def list_jobs(
    status: Optional[str] = None,
    min_score: int = 0,
    max_score: int = 100,
    remote: bool = False,
    india_eligible: bool = False,
    source: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "DESC",
    limit: int = 200,
    offset: int = 0,
):
    jobs = db.get_jobs(
        status=status, min_score=min_score, max_score=max_score,
        remote_only=remote, india_eligible_only=india_eligible, source=source, search=search,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, update: JobUpdate):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if update.status:
        if update.status not in db.VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {db.VALID_STATUSES}")
        db.update_job_status(job_id, update.status)
        db.log_activity("info", f"Job #{job_id} status → {update.status}", f"{job['title']} @ {job['company']}")



    if update.url is not None:
        db.update_job_field(job_id, "url", update.url)

    return db.get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete_job(job_id)
    db.log_activity("warning", f"Job #{job_id} deleted", f"{job['title']} @ {job['company']}")
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    return db.get_stats()


@app.get("/api/sources")
def get_sources():
    """List configured resources together with their latest fetch status."""
    from hirevia.sources import SourceRegistry
    registry = SourceRegistry.from_yaml(os.path.join(_project_root, "sources.yaml"))
    previous = {item["source_id"]: item for item in db.get_source_status()}
    sources = []
    for item in registry.metadata():
        sources.append({**item, **{
            "jobs_collected": previous.get(item["id"], {}).get("jobs_collected", 0),
            "last_success": previous.get(item["id"], {}).get("last_success"),
            "last_error": previous.get(item["id"], {}).get("last_error", ""),
        }})
    return {"sources": sources}


# ─── Search ────────────────────────────────────────────────────────────────

_search_state = {"running": False, "progress": 0, "total": 0, "message": ""}


@app.get("/api/search/status")
def search_status():
    return _search_state


@app.post("/api/search")
def trigger_search(req: SearchRequest, background_tasks: BackgroundTasks):
    if _search_state["running"]:
        raise HTTPException(status_code=409, detail="A search is already in progress")

    background_tasks.add_task(_run_search, req.query, req.location, req.limit, req.no_ai)
    return {"status": "started", "query": req.query}


def _run_search(query: str, location: str, limit: int, no_ai: bool):
    """Background search task that imports and runs hirevia sources."""
    _search_state["running"] = True
    _search_state["progress"] = 0
    _search_state["message"] = f"Searching for '{query}'..."

    db.log_activity("info", f"🔍 Search started: '{query}'", f"location={location}, limit={limit}")

    try:
        from hirevia.sources import SourceRegistry

        companies_path = os.path.join(_project_root, "companies.yaml")

        registry = SourceRegistry.from_yaml(os.path.join(_project_root, "sources.yaml"))
        sources = registry.enabled_sources()

        _search_state["total"] = len(sources)
        all_jobs = []
        source_counts = {}

        with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as pool:
            future_to_source = {
                pool.submit(source.fetch_safely, query, location=location, limit=limit, companies_path=companies_path): source
                for source in sources
            }
            for future in as_completed(future_to_source):
                source = future_to_source[future]
                _search_state["progress"] += 1
                try:
                    result = future.result()
                    if result.error:
                        raise RuntimeError(result.error)
                    source_counts[source.name] = len(result.jobs)
                    all_jobs.extend(result.jobs)
                    db.record_source_status(next(item for item in registry.metadata() if item["id"] == source.source_id), len(result.jobs))
                    _search_state["message"] = f"{source.name}: {len(result.jobs)} jobs"
                    db.log_activity("info", f"  ✓ {source.name}: {len(result.jobs)} jobs")
                except Exception as e:
                    source_counts[source.name] = 0
                    db.record_source_status(next(item for item in registry.metadata() if item["id"] == source.source_id), error=str(e))
                    _search_state["message"] = f"{source.name}: error — {e}"
                    db.log_activity("warning", f"  ✗ {source.name}: {e}")

        # Deduplicate
        seen = set()
        unique = []
        for j in all_jobs:
            key = (j.title.lower().strip(), j.company.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(j)
        all_jobs = unique

        _search_state["message"] = f"Saving {len(all_jobs)} jobs to database..."

        # AI Rating (optional)
        if not no_ai:
            try:
                from hirevia.rating import AIRater, LLM_URL, LLM_MODEL
                from hirevia.models import Profile

                rater = AIRater()
                if rater.available:
                    db.log_activity("info", f"🤖 AI rating with {LLM_MODEL}...")
                    # Load profile
                    profile_path = os.path.join(_project_root, "profile.yaml")
                    profile = None
                    if os.path.exists(profile_path):
                        profile = Profile.from_yaml(profile_path)
                    else:
                        profile = Profile(name="Job Seeker")

                    rater.rate_jobs(all_jobs, profile)
                    db.log_activity("info", "  ✓ AI rating complete")
                else:
                    db.log_activity("warning", "  ⚠ LLM offline — skipping AI rating")
                    for j in all_jobs:
                        j.score = 50
                        j.rating = "No AI"
            except Exception as e:
                db.log_activity("warning", f"  ⚠ AI rating failed: {e}")
                for j in all_jobs:
                    j.score = 50
                    j.rating = "Error"

        # Save to database
        saved = 0
        for j in all_jobs:
            db.upsert_job({
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "url": j.url,
                "description": j.description,
                "salary": j.salary,
                "source": j.source,
                "source_type": j.source_type,
                "original_url": j.original_url,
                "source_metadata": j.source_metadata,
                "remote": j.remote,
                "country": j.country,
                "location_restrictions": j.location_restrictions,
                "timezone": j.timezone,
                "india_eligibility": j.india_eligibility,
                "tags": j.tags,
                "posted": j.posted,
                "score": j.score,
                "rating": j.rating,
                "reasoning": j.reasoning,
                "skills_match": j.skills_match,
                "experience_fit": j.experience_fit,
                "salary_fit": j.salary_fit,
                "remote_fit": j.remote_fit,
            })
            saved += 1

        db.record_search(query, location, len(sources), saved)
        db.log_activity("success", f"✅ Search complete: {saved} jobs saved from {len(source_counts)} sources")

        _search_state["message"] = f"Done! {saved} jobs saved."
    except Exception as e:
        logger.exception("Search failed")
        db.log_activity("error", f"❌ Search failed: {e}")
        _search_state["message"] = f"Error: {e}"
    finally:
        _search_state["running"] = False


# ─── Config ────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config(key: Optional[str] = None):
    val = db.get_config(key)
    if key and val is None:
        # Fall back to reading from files
        if key == "profile_yaml":
            path = os.path.join(_project_root, "profile.yaml")
            if os.path.exists(path):
                return {"key": key, "value": Path(path).read_text()}
            return {"key": key, "value": ""}
        elif key == "companies_yaml":
            path = os.path.join(_project_root, "companies.yaml")
            if os.path.exists(path):
                return {"key": key, "value": Path(path).read_text()}
            return {"key": key, "value": ""}
    if isinstance(val, dict):
        return {"configs": val}
    return {"key": key, "value": val}


@app.put("/api/config")
def update_config(update: ConfigBulkUpdate):
    for k, v in update.configs.items():
        db.set_config(k, v)
        # Also write profile/companies YAML files directly
        if k == "profile_yaml":
            path = os.path.join(_project_root, "profile.yaml")
            Path(path).write_text(v)
            db.log_activity("info", "📝 Profile YAML updated")
        elif k == "companies_yaml":
            path = os.path.join(_project_root, "companies.yaml")
            Path(path).write_text(v)
            db.log_activity("info", "📝 Companies YAML updated")
    return {"ok": True}


@app.put("/api/config/one")
def update_config_one(update: ConfigUpdate):
    db.set_config(update.key, update.value)
    if update.key == "profile_yaml":
        path = os.path.join(_project_root, "profile.yaml")
        Path(path).write_text(update.value)
        db.log_activity("info", "📝 Profile YAML updated")
    elif update.key == "companies_yaml":
        path = os.path.join(_project_root, "companies.yaml")
        Path(path).write_text(update.value)
        db.log_activity("info", "📝 Companies YAML updated")
    return {"ok": True}


# ─── Activity Log ──────────────────────────────────────────────────────────

@app.get("/api/activity")
def get_activity(limit: int = 100, offset: int = 0):
    logs = db.get_activity_log(limit=limit, offset=offset)
    return {"logs": logs, "count": len(logs)}


@app.delete("/api/activity")
def clear_activity():
    with db.get_db() as conn:
        conn.execute("DELETE FROM activity_log")
        conn.commit()
    return {"ok": True}


# ─── Export ────────────────────────────────────────────────────────────────

@app.get("/api/export")
def export_jobs(format: str = "json"):
    jobs = db.get_jobs(limit=10000)
    if format == "csv":
        import csv
        import io
        output = io.StringIO()
        if jobs:
            writer = csv.DictWriter(output, fieldnames=jobs[0].keys())
            writer.writeheader()
            writer.writerows(jobs)
        return PlainTextResponse(output.getvalue(), media_type="text/csv",
                                headers={"Content-Disposition": "attachment; filename=jobs.csv"})
    return {"jobs": jobs}


# ─── Static files ──────────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"

@app.get("/")
def serve_index():
    return FileResponse(str(static_dir / "index.html"))

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=3000, reload=True)
