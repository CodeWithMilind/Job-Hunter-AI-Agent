# 🎯 Hirevia

Hirevia is a single, shared job-search pipeline that runs the same search flow for both the CLI and the dashboard.

The project reuses the existing `Job` model, `SourceRegistry`, built-in sources, cache, database, and AI scoring so there is one authoritative search path instead of two separate implementations.

Telegram is not required for normal job search. The default registry keeps Telegram disabled unless you explicitly opt in.

## Unified architecture

```text
CLI ───────────────┐
                   ↓
             search_jobs()
                   ↓
             SourceRegistry
                   ↓
         Normalize + Deduplicate
                   ↓
            Cache / Database
                   ↓
             AI Scoring
                   ↓
         Final Job List
                   ↓
Frontend/API ──────┘
```

The shared pipeline in `hirevia/pipeline.py` is the source of truth for:

- fetching jobs from enabled sources
- normalizing raw source data
- deduplicating jobs
- applying cache filtering
- scoring jobs with the local AI engine
- returning a single final list of jobs for both interfaces

## What this means in practice

- the CLI only collects arguments and calls `search_jobs()`
- the frontend API calls the same `search_jobs()` pipeline through the dashboard app
- one source failure does not stop the whole run
- empty results are handled cleanly
- the final job objects are returned in a consistent, JSON-friendly structure

## Quick start

### Backend

```bash
cd Hirevia
python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 3000
```

### Frontend

Open the dashboard in a browser:

```text
http://127.0.0.1:3000
```

### CLI

```bash
cd Hirevia
python -m hirevia -q "python developer" --no-ai
python -m hirevia -q "python developer" -p profile.yaml
python -m hirevia
```

Additional CLI options:

```bash
python -m hirevia -q "python developer" --limit 25
python -m hirevia -q "python developer" --cache-days 30
python -m hirevia -q "python developer" --no-cache
python -m hirevia --clear-cache
```

### Frontend search flow

The frontend search uses exactly one API endpoint:

```http
POST /api/search/jobs
```

This endpoint calls the shared `search_jobs()` pipeline in `hirevia/pipeline.py`, which:

1. loads sources from `SourceRegistry`
2. fetches jobs from the enabled sources
3. normalizes the data
4. deduplicates results
5. applies cache
6. scores jobs with the local AI engine when available
7. returns final job JSON to the frontend

No Telegram login or Telegram authentication is required for normal job search.

## Source setup

The registry is driven by `sources.yaml`. Individual sources can be enabled or disabled without changing Python code:

```yaml
sources:
  remotive: { enabled: true }
  remoteok: { enabled: false }
  greenhouse: { enabled: true }
  linkedin: { enabled: false }
  telegram: { enabled: false }
```

Telegram remains fully optional and disabled by default. Normal searches never initialize the Telegram client or require environment variables for it.

## Required environment variables

Normal job search does not require Telegram credentials.

Optional Telegram-only usage requires:

```bash
export TELEGRAM_API_ID="..."
export TELEGRAM_API_HASH="..."
```

These variables are only needed if you explicitly enable the Telegram source in `sources.yaml` and use the Telegram-specific flow.

## Profile setup

Create `profile.yaml` with your details so the AI scorer can rank jobs against your profile:

```yaml
name: "Your Name"
title: "Software Engineer"
experience_years: 5
skills: [Python, Docker, AWS, React]
desired_roles: [Backend Engineer, SRE]
salary_min: 100000
salary_max: 160000
location_preference: "Remote"
remote_ok: true
industries: [Fintech, SaaS]
```

## Job fields and JSON output

The shared pipeline returns final jobs with useful fields such as:

- title
- company
- location
- source
- url
- description
- score
- rating
- skills_match
- experience_fit
- salary_fit
- remote_fit

## Local AI scoring

If a local model is available, Hirevia scores jobs against the profile using the same AI logic for both the CLI and the dashboard.

If no local LLM is detected, the pipeline falls back to neutral scores instead of failing the search.

## Caching

The persistent seen-jobs cache prevents re-reviewing the same positions across runs. It is shared by the unified pipeline and is still optional via `--no-cache`.

## Notes

- Telegram support remains optional and will not crash the app when the dependency is missing.
- The dashboard and CLI both stay compatible with the existing sources and storage model.
- No duplicate scraper logic was introduced for the frontend.

## Verification

The project test suite is the current verification gate:

```bash
python -m pytest -q
```

This ensures the shared pipeline, source registry, and compatibility wrappers continue to work together.

## Test instructions

```bash
python -m pytest -q
```

Also verify the frontend API contract directly:

```bash
python -c "import sys; sys.path.insert(0, '.'); from fastapi.testclient import TestClient; import dashboard.app as app_mod; client = TestClient(app_mod.app); resp = client.post('/api/search/jobs', json={'query': 'Python Developer', 'location': '', 'limit': 10, 'no_ai': True}); print(resp.status_code); print(resp.json()['count'])"
```

A successful response returns HTTP 200 and a JSON list of jobs.

# Use it with Hirevia
python -m hirevia -q "python dev" --llm-model qwen3:8b
```

Or set it permanently in your environment:
```bash
export LLM_MODEL="qwen3:8b"
python -m hirevia -q "python dev"
```

## Architecture

```
hire-via/
├── setup.sh              # Smart installer (Linux/macOS)
├── setup.ps1             # Smart installer (Windows)
├── uninstall.sh          # Uninstaller (Linux/macOS)
├── uninstall.ps1         # Uninstaller (Windows)
├── package.json          # npm wrapper
├── profile.yaml          # Your profile (edit this)
├── companies.yaml        # ATS company slugs (edit this)
├── sources.yaml          # Enable/disable configured job resources
├── hirevia/
│   ├── models.py         # Job and Profile dataclasses
│   ├── rating.py         # Local LLM rating with retry logic
│   ├── cache.py          # SQLite seen-jobs cache
│   ├── display.py        # Rich terminal UI
│   ├── cli.py            # Search pipeline + argparse
│   └── sources/
│       ├── base.py       # JobSource interface + isolated fetch result
│       ├── registry.py   # YAML-configured built-in source registry
│       ├── remotive.py   # Remotive API
│       ├── arbeitnow.py  # Arbeitnow API (paginated)
│       ├── remoteok.py   # RemoteOK API
│       ├── jobicy.py     # Jobicy API
│       ├── himalayas.py  # Himalayas API
│       ├── greenhouse.py # Greenhouse ATS (direct API)
│       ├── ashby.py      # Ashby ATS (direct API)
│       └── linkedin.py   # LinkedIn scraping (opt-in)
└── dashboard/
    ├── app.py            # FastAPI backend
    ├── database.py       # SQLite storage
    ├── run.sh            # Dashboard launcher
    └── static/
        └── index.html    # Dark-mode SPA
```

## A Note on AI in Job Search

We built Hirevia because job searching is exhausting and the tools out there either dump too many listings on you or try to automate the whole thing. We think the sweet spot is: **let the machine do the grunt work (searching, filtering, summarizing) and keep the human making the actual decisions.**

The AI scoring is there to save you time reading through listings, not to tell you what to apply for. A 95/100 score doesn't mean "apply immediately" — it means "this one looks relevant, worth a closer look." A 40/100 doesn't mean "skip it" — it might be a role you'd love that the AI just doesn't have enough context for.

**You are the loop. The AI is just the filter.**

## ⚠️ LinkedIn Warning

LinkedIn scraping is **off by default**. It depends on undocumented HTML that breaks constantly and might violate their ToS. We keep it around because sometimes it's useful, but we'd rather you know the tradeoff:

```bash
python -m hirevia -q "python dev" --enable-linkedin
```

## Uninstalling

Run the uninstaller in the project directory — it removes everything the installer created (venv, models, binaries, caches, dashboard database) but **keeps your `profile.yaml` and `companies.yaml`**:

```bash
# Linux / macOS
bash uninstall.sh

# Windows
.\uninstall.ps1
```

Options:

| Flag | What it does |
|------|-------------|
| `--purge` / `-Purge` | Also delete `profile.yaml`, `companies.yaml`, `results.csv`, `results.json` |
| `--keep-cache` / `-KeepCache` | Keep the `~/.hirevia` seen-jobs cache |

Safe to re-run — a second run just reports what's already gone. Uninstalling does **not** remove Ollama or the models you pulled into it; those are managed separately (`ollama rm qwen3:1.7b` if you want them gone).

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE)
