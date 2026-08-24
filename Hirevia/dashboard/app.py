"""Hirevia Dashboard — FastAPI backend."""

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yaml

# Make sure both the project and dashboard modules are importable.
_project_root = str(Path(__file__).resolve().parent.parent)
_dashboard_dir = str(Path(__file__).resolve().parent)
for entry in (_project_root, _dashboard_dir):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import database as db
from hirevia.quality import LinkState, freshness_score, is_expired, is_relevant, profile_qualified, verify_application_link

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

class TelegramFetchRequest(BaseModel):
    query: str = ""
    limit: int = 50


_monitor_state = {
    "active": False,
    "last_scan": None,
    "next_scan": None,
    "jobs_scanned": 0,
    "jobs_matched": 0,
    "important_jobs": 0,
    "scan_stats": {},
    "error": "",
}
_monitor_stop = threading.Event()
_monitor_thread: Optional[threading.Thread] = None
MONITOR_INTERVAL_SECONDS = 15
_reset_generation = 0


def _profile():
    from hirevia.models import Profile
    profile_path = os.path.join(_project_root, "profile.yaml")
    return Profile.from_yaml(profile_path) if os.path.exists(profile_path) else Profile(name="Job Seeker")


def _save_jobs(jobs: list[Any]) -> None:
    for job in jobs:
        payload = {
            "title": job.title, "company": job.company, "location": job.location,
            "url": job.url, "description": job.description, "salary": job.salary,
            "source": job.source, "source_type": job.source_type,
            "original_url": job.original_url, "source_metadata": job.source_metadata,
            "remote": job.remote, "country": job.country,
            "location_restrictions": job.location_restrictions, "timezone": job.timezone,
            "india_eligibility": job.india_eligibility, "tags": job.tags,
            "posted": job.posted, "score": job.score, "rating": job.rating,
            "reasoning": job.reasoning, "skills_match": job.skills_match,
            "experience_fit": job.experience_fit, "salary_fit": job.salary_fit,
            "remote_fit": job.remote_fit,
        }
        db.upsert_job(payload)


def _notify_telegram(jobs: list[Any]) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return 0
    state_path = Path(_project_root) / "telegram_delivery.json"
    try:
        sent = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else []
    except (OSError, ValueError):
        sent = []
    sent_ids = set(sent)
    important = [job for job in jobs if job.score >= 0]
    delivered = 0
    import requests
    for job in important:
        identity = (job.url or f"{job.company}|{job.title}|{job.location}").lower().strip()
        if identity in sent_ids:
            continue
        metadata = job.source_metadata or {}
        experience = metadata.get("experience") or metadata.get("experience_years") or "Entry level / not specified"
        skills = metadata.get("skills") or ", ".join(job.tags) or "Not specified"
        text = (
            "🔔 NEW JOB\n\n"
            f"{job.title}\n{job.company}\n\n📍 {job.location}\n"
            f"Experience: {experience}\nMatch: {job.score}%\nDetected: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n"
            f"Key skills: {skills}\n\nApply: {job.url or 'Open Hirevia'}"
        )
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text}, timeout=10,
            )
            response.raise_for_status()
            sent_ids.add(identity)
            delivered += 1
        except requests.RequestException as exc:
            logger.warning("Telegram notification failed: %s", exc)
    state_path.write_text(json.dumps(sorted(sent_ids)), encoding="utf-8")
    return delivered


def _monitor_scan() -> None:
    generation = _reset_generation
    from hirevia.pipeline import search_jobs as run_pipeline
    profile = _profile()
    query = " OR ".join(profile.target_roles or profile.desired_roles or ["Software Engineer"])
    scan_stats: Dict[str, Any] = {}
    jobs = run_pipeline(
        query=query, location="India", profile=profile, ai_enabled=False,
        limit=int(profile.settings.get("max_results", 200)), companies_path=os.path.join(_project_root, "companies.yaml"),
        sources_path=os.path.join(_project_root, "sources.yaml"), no_cache=False,
        show_output=False, scan_stats=scan_stats,
        source_ids=profile.sources or None,
    )
    if generation != _reset_generation:
        return
    _save_jobs(scan_stats.get("processed_jobs", jobs))
    for source_id, source in scan_stats.get("sources", {}).items():
        db.record_source_status(
            {"id": source_id, "name": source["name"], "type": source["type"], "enabled": True},
            jobs_collected=source["jobs"], error=source["error"],
        )
    telegram_sent = _notify_telegram(jobs)
    _monitor_state.update({
        "last_scan": time.time(), "next_scan": time.time() + MONITOR_INTERVAL_SECONDS,
        "jobs_scanned": db.get_stats().get("scanned", 0),
        "jobs_matched": db.get_stats().get("matched", len(jobs)),
        "important_jobs": _monitor_state["important_jobs"] + sum(job.score >= 65 for job in jobs),
        "telegram_sent": _monitor_state.get("telegram_sent", 0) + telegram_sent,
        "error": "",
        "scan_stats": scan_stats,
    })
    db.log_activity("success", f"Monitoring scan completed: {len(jobs)} matched jobs")


def _monitor_loop() -> None:
    while not _monitor_stop.is_set():
        try:
            _monitor_scan()
        except Exception as exc:
            _monitor_state["error"] = str(exc)
            _monitor_state["last_scan"] = time.time()
            _monitor_state["next_scan"] = time.time() + MONITOR_INTERVAL_SECONDS
            db.log_activity("error", f"Monitoring scan failed: {exc}")
        _monitor_stop.wait(MONITOR_INTERVAL_SECONDS)
    _monitor_state["active"] = False


@app.get("/api/search-strategy")
def get_search_strategy():
    profile = _profile()
    return {"primary_roles": profile.target_roles or profile.desired_roles, "skills": profile.keywords or profile.skills, "locations": profile.locations, "experience_terms": profile.experience}


@app.get("/api/monitoring")
def monitoring_status():
    return {**_monitor_state, "interval_seconds": MONITOR_INTERVAL_SECONDS}


@app.get("/api/telegram/status")
def telegram_status():
    """Report whether the configured reusable Telegram session is available."""
    from hirevia.sources.telegram import TelegramClient, TelegramSearch

    config_path = Path(_project_root) / "sources.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        configured = bool(config.get("sources", {}).get("telegram", {}).get("enabled", False))
    except (OSError, TypeError, AttributeError, yaml.YAMLError):
        configured = False
    session_exists = Path(TelegramSearch._get_session_file() + ".session").exists()
    credentials = bool(os.getenv("TELEGRAM_API_ID") and os.getenv("TELEGRAM_API_HASH"))
    connected = bool(configured and TelegramClient is not None and credentials and session_exists)
    return {
        "status": "Connected" if connected else "Disconnected",
        "connected": connected,
        "configured": configured,
        "session_available": session_exists,
    }


@app.post("/api/monitoring/start")
def start_monitoring():
    global _monitor_thread
    if _monitor_state["active"]:
        return monitoring_status()
    _monitor_stop.clear()
    _monitor_state.update({"active": True, "error": "", "next_scan": time.time()})
    _monitor_thread = threading.Thread(target=_monitor_loop, name="hirevia-monitor", daemon=True)
    _monitor_thread.start()
    db.log_activity("info", "Monitoring started")
    return monitoring_status()


@app.post("/api/monitoring/stop")
def stop_monitoring():
    _monitor_stop.set()
    _monitor_state["active"] = False
    _monitor_state["next_scan"] = None
    db.log_activity("info", "Monitoring stopped")
    return monitoring_status()

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
    matched: Optional[bool] = None,
):
    profile = _profile() if matched is not None else None
    jobs = db.get_jobs(
        status=status, min_score=min_score, max_score=max_score,
        remote_only=remote, india_eligible_only=india_eligible, source=source, search=search,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
        profile=profile or _profile(), matched=matched,
    )
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/api/jobs/telegram")
def list_telegram_jobs(
    search: Optional[str] = None,
    sort_by: str = "posted",
    sort_order: str = "DESC",
    limit: int = 200,
    offset: int = 0,
):
    """Return only Telegram jobs already stored by the unified pipeline."""
    jobs = db.get_jobs(
        source="Telegram",
        search=search,
        sort_by="created_at" if sort_by == "posted" else sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    now = time.time()
    recent_cutoff = now - 7 * 86400
    recent = [job for job in jobs if job.get("created_at", 0) >= recent_cutoff]
    remote = [job for job in jobs if job.get("remote")]
    return {
        "jobs": jobs,
        "count": len(jobs),
        "stats": {
            "total": len(jobs),
            "recent": len(recent),
            "remote": len(remote),
        },
    }


def _telegram_identity(job: Any) -> str:
    metadata = job.get("source_metadata", {}) if isinstance(job, dict) else (job.source_metadata or {})
    url = job.get("url", "") if isinstance(job, dict) else job.url
    telegram_url = metadata.get("telegram_message_url", "")
    application_url = url if url and "t.me/" not in url and "telegram.me/" not in url else ""
    return (
        application_url
        or telegram_url
        or url
        or f"{metadata.get('channel_username', '')}:{metadata.get('telegram_message_id', '')}"
    ).strip().lower()


def _telegram_job_dict(job: Any) -> Dict[str, Any]:
    return {
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
    }


@app.post("/api/telegram/fetch")
def fetch_telegram_jobs(request: TelegramFetchRequest):
    """Fetch Telegram jobs and persist them through the existing jobs table."""
    config_path = Path(_project_root) / "sources.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        telegram_config = config.get("sources", {}).get("telegram", {})
        enabled = isinstance(telegram_config, dict) and bool(telegram_config.get("enabled", False))
    except Exception as exc:
        logger.warning("Telegram configuration could not be read: %s", exc)
        return {"success": False, "status": "error", "message": "Telegram configuration could not be read.", "fetched": 0, "new_jobs": 0, "duplicates": 0}

    if not enabled:
        return {"success": False, "status": "disabled", "message": "Telegram is disabled in sources.yaml.", "fetched": 0, "new_jobs": 0, "duplicates": 0}

    try:
        from hirevia.pipeline import search_jobs as run_pipeline
        telegram_scan_stats: Dict[str, Any] = {}
        jobs = run_pipeline(
            query=request.query.strip() or "technology intern", location="India",
            profile=_profile(), ai_enabled=False, limit=request.limit,
            sources_path=str(config_path), no_cache=True, show_output=False,
            source_ids=["telegram"], scan_stats=telegram_scan_stats,
        )
        fetched_jobs = telegram_scan_stats.get("processed_jobs", jobs)
        existing = db.get_jobs(source="Telegram", limit=10000)
        existing_ids = {_telegram_identity(job) for job in existing if _telegram_identity(job)}
        new_count = 0
        duplicate_count = 0
        for job in fetched_jobs:
            identity = _telegram_identity(job)
            if identity and identity in existing_ids:
                duplicate_count += 1
                continue
            db.upsert_job(_telegram_job_dict(job))
            new_count += 1
            if identity:
                existing_ids.add(identity)
        db.log_activity("success", f"Telegram fetch: {new_count} new jobs, {duplicate_count} duplicates skipped")
        return {
            "success": True,
            "status": "completed",
            "message": f"Fetched {new_count} new jobs. {duplicate_count} duplicates skipped." if new_count or duplicate_count else "No new Telegram jobs found.",
            "fetched": len(fetched_jobs),
            "new_jobs": new_count,
            "duplicates": duplicate_count,
        }
    except Exception as exc:
        logger.warning("Telegram fetch failed: %s", exc)
        return {"success": False, "status": "error", "message": "Telegram fetch failed. Check Telegram authentication and channel access.", "fetched": 0, "new_jobs": 0, "duplicates": 0}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["match_status"] = "matched" if profile_qualified(job, _profile()) else "scanned"
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
    stats = db.get_stats(_profile())
    stats["new_jobs"] = _monitor_state.get("scan_stats", {}).get("new", 0)
    return stats


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


def _write_profile_yaml(content: str) -> None:
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid profile.yaml: {exc}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="profile.yaml must contain a YAML mapping")
    required = {"target_roles", "keywords", "locations", "experience", "exclude_keywords", "settings"}
    missing = sorted(required - set(parsed))
    if missing:
        raise HTTPException(status_code=400, detail=f"profile.yaml missing required keys: {', '.join(missing)}")
    path = Path(_project_root) / "profile.yaml"
    temporary = path.with_suffix(".yaml.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        from hirevia.models import Profile
        Profile.from_yaml(str(temporary))
        temporary.replace(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid profile.yaml: {exc}")


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

    scan_stats: Dict[str, Any] = {}
    jobs = run_pipeline(
        query=req.query,
        location=req.location,
        profile=profile,
        ai_enabled=not req.no_ai,
        limit=req.limit,
        companies_path=os.path.join(_project_root, "companies.yaml"),
        sources_path=os.path.join(_project_root, "sources.yaml"),
        no_cache=req.no_cache,
        show_output=False, scan_stats=scan_stats,
    )

    payload_jobs = []
    for job in scan_stats.get("processed_jobs", jobs):
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
        if profile_qualified(job, profile):
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
        scan_stats: Dict[str, Any] = {}

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
            scan_stats=scan_stats,
        )

        _search_state["total"] = len(jobs)
        _search_state["message"] = f"Saving {len(jobs)} jobs to database..."

        saved = 0
        for job in scan_stats.get("processed_jobs", jobs):
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
    global _reset_generation
    from hirevia.cache import SeenJobsCache

    _reset_generation += 1
    deleted_count = db.clear_runtime_data()
    
    # Clear seen-jobs cache so previously seen jobs can reappear
    try:
        cache = SeenJobsCache()
        cache.clear()
        cache.close()
    except Exception as e:
        logger.warning(f"Could not clear seen-jobs cache: {e}")
    delivery_state = Path(_project_root) / "telegram_delivery.json"
    try:
        delivery_state.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not clear Telegram delivery state: %s", e)
    _search_state.update({"running": False, "progress": 0, "total": 0, "message": ""})
    _monitor_state.update({
        "last_scan": None, "next_scan": time.time() if _monitor_state["active"] else None,
        "jobs_scanned": 0, "jobs_matched": 0, "important_jobs": 0,
        "error": "", "telegram_sent": 0, "scan_stats": {},
    })
    return {"ok": True, "deleted_count": deleted_count, "message": "All runtime job data deleted."}


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
        if k == "profile_yaml":
            _write_profile_yaml(v)
            db.log_activity("info", "📝 Profile YAML updated")
        elif k == "companies_yaml":
            path = os.path.join(_project_root, "companies.yaml")
            Path(path).write_text(v)
            db.log_activity("info", "📝 Companies YAML updated")
        else:
            db.set_config(k, v)
    return {"ok": True}


@app.put("/api/config/one")
def update_config_one(update: ConfigUpdate):
    if update.key == "profile_yaml":
        _write_profile_yaml(update.value)
        db.log_activity("info", "📝 Profile YAML updated")
    elif update.key == "companies_yaml":
        path = os.path.join(_project_root, "companies.yaml")
        Path(path).write_text(update.value)
        db.log_activity("info", "📝 Companies YAML updated")
    else:
        db.set_config(update.key, update.value)
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
