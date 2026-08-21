"""Hirevia Dashboard — FastAPI backend."""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make sure both the project and dashboard modules are importable.
_project_root = str(Path(__file__).resolve().parent.parent)
_dashboard_dir = str(Path(__file__).resolve().parent)
for entry in (_project_root, _dashboard_dir):
    if entry not in sys.path:
        sys.path.insert(0, entry)

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
    no_cache: bool = True

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


@app.post("/api/search/jobs")
def search_jobs_json(req: SearchRequest):
    """Unified frontend search endpoint: one job-search path for all UI searches."""
    from hirevia.pipeline import search_jobs as run_pipeline
    from hirevia.models import Profile

    profile_path = os.path.join(_project_root, "profile.yaml")
    profile = Profile.from_yaml(profile_path) if os.path.exists(profile_path) else Profile(name="Job Seeker")

    jobs = run_pipeline(
        query=req.query,
        location=req.location,
        profile=profile,
        ai_enabled=not req.no_ai,
        limit=req.limit,
        companies_path=os.path.join(_project_root, "companies.yaml"),
        sources_path=os.path.join(_project_root, "sources.yaml"),
        no_cache=req.no_cache,
        show_output=False,
    )

    payload_jobs = []
    for job in jobs:
        payload = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source": job.source,
            "source_type": job.source_type,
            "url": job.url,
            "description": job.description,
            "salary": job.salary,
            "remote": job.remote,
            "posted": job.posted,
            "score": job.score,
            "rating": job.rating,
            "reasoning": job.reasoning,
            "skills_match": job.skills_match,
            "experience_fit": job.experience_fit,
            "salary_fit": job.salary_fit,
            "remote_fit": job.remote_fit,
            "india_eligibility": job.india_eligibility,
            "source_metadata": job.source_metadata,
        }
        db.upsert_job({
            **payload,
            "original_url": job.original_url,
            "country": job.country,
            "location_restrictions": job.location_restrictions,
            "timezone": job.timezone,
            "tags": job.tags,
        })
        payload_jobs.append(payload)

    db.record_search(req.query, req.location, len(jobs), len(payload_jobs))
    return {"jobs": payload_jobs, "count": len(payload_jobs), "status": "completed"}


def _run_search(query: str, location: str, limit: int, no_ai: bool):
    """Run the unified Hirevia search pipeline and persist the result set."""
    _search_state["running"] = True
    _search_state["progress"] = 0
    _search_state["message"] = f"Searching for '{query}'..."

    db.log_activity("info", f"🔍 Search started: '{query}'", f"location={location}, limit={limit}")

    try:
        from hirevia.pipeline import search_jobs
        from hirevia.models import Profile

        profile_path = os.path.join(_project_root, "profile.yaml")
        profile = Profile.from_yaml(profile_path) if os.path.exists(profile_path) else Profile(name="Job Seeker")

        jobs = search_jobs(
            query=query,
            location=location,
            profile=profile,
            ai_enabled=not no_ai,
            limit=limit,
            companies_path=os.path.join(_project_root, "companies.yaml"),
            sources_path=os.path.join(_project_root, "sources.yaml"),
            no_cache=False,
            show_output=False,
        )

        _search_state["total"] = len(jobs)
        _search_state["message"] = f"Saving {len(jobs)} jobs to database..."

        saved = 0
        for job in jobs:
            db.upsert_job({
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "description": job.description,
                "salary": job.salary,
                "source": job.source,
                "source_type": job.source_type,
                "original_url": job.original_url,
                "source_metadata": job.source_metadata,
                "remote": job.remote,
                "country": job.country,
                "location_restrictions": job.location_restrictions,
                "timezone": job.timezone,
                "india_eligibility": job.india_eligibility,
                "tags": job.tags,
                "posted": job.posted,
                "score": job.score,
                "rating": job.rating,
                "reasoning": job.reasoning,
                "skills_match": job.skills_match,
                "experience_fit": job.experience_fit,
                "salary_fit": job.salary_fit,
                "remote_fit": job.remote_fit,
            })
            saved += 1

        db.record_search(query, location, len(jobs), saved)
        db.log_activity("success", f"✅ Search complete: {saved} jobs saved from pipeline")

        _search_state["message"] = f"Done! {saved} jobs saved."
    except Exception as e:
        logger.exception("Search failed")
        db.log_activity("error", f"❌ Search failed: {e}")
        _search_state["message"] = f"Error: {e}"
    finally:
        _search_state["running"] = False


# ─── Reset (Safe Wipe) ──────────────────────────────────────────────────────

@app.post("/api/reset")
def reset_all_records():
    """Delete all job records and clear the seen-jobs cache for a clean slate test.
    
    SAFE: This only deletes job records. Configuration, sources, profile, and settings remain intact.
    """
    from hirevia.cache import SeenJobsCache
    
    deleted_count = db.delete_all_jobs()
    
    # Clear seen-jobs cache so previously seen jobs can reappear
    try:
        cache = SeenJobsCache()
        cache.clear()
        cache.close()
    except Exception as e:
        logger.warning(f"Could not clear seen-jobs cache: {e}")
    
    db.log_activity("warning", f"🗑️ Dashboard reset: {deleted_count} job records deleted, cache cleared")
    return {"ok": True, "deleted_count": deleted_count, "message": "All job records deleted."}


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
