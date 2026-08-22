# 🎯 Hirevia

Hirevia is a single, shared job-search pipeline that runs the same search flow for both the CLI and the dashboard.

The project reuses the existing `Job` model, `SourceRegistry`, built-in sources, cache, database, and AI scoring so there is one authoritative search path instead of two separate implementations.

## Autonomous monitoring workflow

The dashboard is a small monitoring and debugging interface:

```text
Edit `profile.yaml` once with target roles, keywords, locations, exclusions, and scan settings
-> Start Monitoring
-> Every 15 seconds, all enabled sources and configured Telegram feeds are checked
-> Jobs are normalized, filtered, deduplicated, scored, saved, and sent to Telegram
-> Valid matches appear in the dashboard
```

No resume upload, extraction, or candidate JSON is required. `profile.yaml` is the only user preference configuration.

## Phase 1 quality gates

The shared pipeline now keeps fewer, fresher, more relevant opportunities:

```text
Query intent -> discovery -> normalize -> expiration -> stable deduplication
-> deterministic relevance -> semantic LLM relevance -> freshness
-> application-link state -> cache -> profile scoring -> final ranking
```

- Explicitly closed or expired jobs and passed deadlines are rejected. Old posted dates alone are not rejected, and invalid dates remain safe.
- Duplicate identity prefers normalized application URLs, source/external IDs, and then company + role + location. Tracking parameters and trailing slashes are normalized.
- Freshness is deterministic from published/posted/updated timestamps with bands for under 1 hour, 6 hours, 24 hours, 1-3 days, and older jobs.
- Application links are classified as `verified_active`, `verified_unavailable`, or `unknown`. Only definitive 404/410-style unavailability is filtered; network failures remain unknown.
- Multi-word searches use title-dominant relevance with skills/technology support and role variations. Obvious conflicts such as Java Developer for a Python Developer search are excluded. Single-word discovery remains broad.

Known limitations: public source APIs do not expose consistent deadlines or external IDs, and link verification can be unknown when sites require authentication, block automated requests, or time out.

## Optional LLM relevance

When AI is enabled, Hirevia uses the existing local LLM integration in two connected stages: query understanding creates a structured intent, then a bounded set of deterministic candidates is evaluated semantically. LLM results are stored in safe job metadata, directly remove low-confidence irrelevant candidates, and influence final ranking alongside deterministic relevance, freshness, application quality, and profile scoring. Query intents and job evaluations are cached in memory for the process lifetime. Invalid JSON, incomplete output, timeouts, unavailable models, and connection failures fall back to deterministic results.

Configure an existing local endpoint with `LLM_ENABLED=true`, `LLM_BASE_URL` (or the existing `LLM_URL`), and `LLM_MODEL`. The dashboard's **No AI** option disables all LLM calls and returns deterministic results with `AI disabled` instead of a synthetic AI score. `LLM_CANDIDATE_LIMIT` caps semantic candidates at 30 by default. Prompts contain only query intent and public job fields; credentials and internal IDs are excluded.

`LLM_TIMEOUT_SECONDS` bounds model requests and defaults to 20 seconds, allowing a bounded cold model load while preventing long per-job stalls.

## NVIDIA multi-model AI

The optional NVIDIA path uses one reusable OpenAI-compatible client with three configured responsibilities:

- `QUERY_MODEL` analyzes the user's query into structured intent.
- `JOB_ANALYSIS_MODEL` evaluates bounded deterministic candidates.
- `RANKING_MODEL` performs one final candidate-ranking call.

Configure these with `NVIDIA_API_KEY` and `NVIDIA_BASE_URL` in the ignored `.env` file. `LLM_MAX_CANDIDATES` limits job-analysis and ranking candidates. No AI mode makes zero NVIDIA or local-LLM calls. Any model timeout, API failure, invalid JSON, or unavailable model falls back to deterministic filtering and ranking; prompts never contain credentials or internal IDs.

Telegram ingestion is enabled in the checked-in configuration; missing Telethon, credentials, or an authorized session are handled without breaking other sources. Bot delivery is optional and uses `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` when configured.

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
                  Quality gates + Cache / Database
                   ↓
             AI Scoring
                   ↓
         Final Job List
                   ↓
Frontend/API ──────┘
```

## Source coverage and compliance

The registry currently supports Remotive, Arbeitnow, RemoteOK, Jobicy, Himalayas, Greenhouse, Ashby, and public Telegram channels. LinkedIn is retained as a disabled opt-in adapter. Indeed, Naukri, Glassdoor, Wellfound, Internshala, Cutshort, Foundit, Hirist, TimesJobs, Workday, and other login/anti-bot platforms remain unsupported until a permitted API, partner integration, or public feed is available. Hirevia does not bypass authentication, CAPTCHA, robots rules, anti-bot controls, or rate limits.

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

Telegram remains optional for normal searches. Monitoring uses it when enabled and configured; normal searches never require Telegram credentials.

## Telegram configuration

Telegram is fully optional and integrated into the unified pipeline as just another job source.

### Why Telegram is optional

- Normal job search works perfectly without Telegram enabled
- Telegram uses the optional Telethon library when it is available
- Telegram requires API credentials (API_ID, API_HASH)
- Telegram requires a persistent session after first authentication
- The checked-in configuration has Telegram enabled; missing credentials still skip it safely

### Setting up Telegram (optional)

1. **Get Telegram API credentials:**
   - Visit https://my.telegram.org/
   - Log in with your Telegram account
   - Go to "API development tools"
   - Create a new application
   - Copy `api_id` and `api_hash`

2. **Set environment variables:**
   ```bash
   export TELEGRAM_API_ID="your_api_id"
   export TELEGRAM_API_HASH="your_api_hash"
   ```

3. **Create telegram.yaml** with job channels:
   ```yaml
   channels:
     - name: "Job Channel 1"
       username: "@job_channel_1"
       enabled: true
       limit: 50
     - name: "Job Channel 2"
       username: "@job_channel_2"
       enabled: true
       limit: 50
   ```

4. **Enable Telegram in sources.yaml:**
   ```yaml
   telegram: { enabled: true }
   ```

5. **Authenticate the session:**
   The first authenticated Telethon session must be created separately with your
   normal Telethon login flow. Hirevia connects only when that session is already
   authorized, so a dashboard search never hangs waiting for phone/code input.
   The session is saved at the project root as `job_hunter_session.session` and
   reused automatically from both the project root and `dashboard/`.

### How Telegram works

1. Telegram source is fetched during normal searches alongside GitHub, Greenhouse, etc.
2. Hirevia reads configured channels for job postings
3. Job detection filters out non-job content (courses, webinars, promotions)
4. Job titles, companies, locations, URLs are extracted when possible
5. Results are deduplicated and scored with the AI engine
6. Jobs appear in the same dashboard/CLI output as other sources

### Telegram failures don't break other sources

If Telegram fails:
- Missing credentials → skipped gracefully
- Network error → skipped with warning
- Authentication error → skipped with warning
- Jobs from GitHub, Greenhouse, etc. still return normally

### Session management

- Session file: `job_hunter_session.session` (project root)
- Session file is in `.gitignore` (never committed)
- Session is reused automatically (no re-auth needed on every search)
- To force re-authentication: delete `job_hunter_session.session`

### Required environment variables

Normal job search does not require Telegram credentials.

Optional Telegram-only usage requires:

```bash
export TELEGRAM_API_ID="your_api_id"
export TELEGRAM_API_HASH="your_api_hash"
```

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
