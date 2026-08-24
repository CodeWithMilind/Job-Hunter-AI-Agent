# 🎯 Hirevia

Hirevia is a single, shared job-search pipeline that runs the same search flow for both the CLI and the dashboard.

The project reuses the existing `Job` model, `SourceRegistry`, built-in sources, cache, database, and AI scoring so there is one authoritative search path instead of two separate implementations.

## Scan and matching pipeline

The dashboard is a small monitoring and debugging interface:

```text
Raw discovered jobs -> normalized jobs -> deduplicated jobs -> stored/scanned jobs
-> deterministic match score -> matched jobs (score >= 50), sorted by score
```

All deduplicated jobs are retained as scanned records, including jobs with
missing descriptions, locations, or experience data. Matching is a score, not
an AND filter: target-role variants, profile keywords, preferred locations, and
early-career evidence increase the score; explicit senior titles or excessive
experience requirements reduce it. The profile in `profile.yaml` supplies the
roles, skills, locations, experience terms, exclusions, enabled sources, and
result settings used by the pipeline.

Jobs below the threshold remain visible on Scanned and Jobs but do not appear
on Matched. Dashboard counts are read from the same stored records, while New
Jobs is based on the latest discovery/cache result. Historical records are
kept in the database rather than silently deleted.

## Autonomous monitoring workflow

Start Monitoring to run the shared pipeline in one background worker every 15
seconds. Manual Search calls that same normalization, deduplication, scoring,
and persistence path. Each enabled source is isolated, so a source error is
recorded in Sources and does not stop the rest of the scan. Telegram remains an
optional source and delivery channel.

## Dashboard

Run the admin dashboard with `python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 3000`.
The dark dashboard includes live monitoring, scanned and matched job views, source health,
Telegram status, and an editable `profile.yaml` editor. Search and save actions use the
existing dashboard APIs; no job-matching logic is duplicated in the frontend.

## Data quality

The shared pipeline keeps the complete processed dataset and ranks relevant
opportunities:

```text
Query intent -> discovery -> normalize -> stable deduplication
-> store scanned records -> profile scoring -> matched ranking
```

- Explicitly closed or expired jobs are retained as scanned records but do not match. Old posted dates alone are not rejected, and invalid dates remain safe.
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

The registry currently supports Greenhouse, LinkedIn as a disabled opt-in adapter, and public Telegram channels. Naukri, Internshala, Unstop, Cutshort, Wellfound, Hirist, and other login/anti-bot platforms are not implemented in this checkout. Hirevia does not bypass authentication, CAPTCHA, robots rules, anti-bot controls, or rate limits.

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
  greenhouse: { enabled: true }
  linkedin: { enabled: false }
  telegram: { enabled: false }
```

Telegram remains optional for normal searches. Monitoring uses it when enabled and configured; normal searches never require Telegram credentials.

## Telegram configuration

Telegram is an optional public-channel source integrated into the unified pipeline as just another job source.

### Telegram setup

1. Install dependencies into the existing environment:
   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Get Telegram API credentials:**
   - Visit https://my.telegram.org/
   - Log in with your Telegram account
   - Go to "API development tools"
   - Create a new application
   - Copy `api_id` and `api_hash`

3. **Set environment variables** (never commit these values):
   ```bash
   export TELEGRAM_API_ID="your_api_id"
   export TELEGRAM_API_HASH="your_api_hash"
   ```

4. **Configure public channels in `telegram.yaml`:**
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

5. **Enable Telegram in `sources.yaml`:**
   ```yaml
   telegram: { enabled: true }
   ```

6. **Authenticate and verify once:**
   Run `python -m hirevia.sources.telegram` from the project root. On the first
   run, Telethon may request the normal interactive Telegram login code. Later
   scans reuse `job_hunter_session.session` and do not ask for login again.

### How Telegram works

1. The monitoring worker calls Telegram through `SourceRegistry` every 15 seconds.
2. New messages after each channel's saved ID are fetched and parsed into `Job`.
3. The shared pipeline applies India, fresher, role, deduplication, freshness, and link gates.
4. Valid jobs are saved to the dashboard database and appear at `/api/jobs`.

### Telegram failures don't break other sources

If Telegram fails, the source records the actionable error in the scan log and
source status; other collectors continue normally.

### Verify Telegram

```powershell
.venv\Scripts\python.exe -m hirevia.sources.telegram
```

The command reports authentication status, channels checked, messages fetched,
jobs extracted, duplicates removed, and final jobs returned. Missing
`TELEGRAM_API_ID` or `TELEGRAM_API_HASH` is reported by name without exposing
secret values.

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

Edit `profile.yaml` to control the deterministic job search:

```yaml
target_roles: [Data Scientist, Data Analyst, Python Developer]
keywords: [Python, SQL, Pandas, FastAPI]
locations: [India, Pune, Remote India]
experience: [internship, fresher, entry level]
exclude_keywords: [Senior, Lead, Manager, "3+ years"]
settings:
   scan_interval_seconds: 15
   max_results: 200
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
│       ├── greenhouse.py # Greenhouse ATS (direct API)
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
