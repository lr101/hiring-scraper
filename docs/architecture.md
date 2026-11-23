# Architecture

Radius discovers German employers, finds their public career pages and job feeds,
keeps those postings current, and ranks them against a saved profile. It also
tracks applications and can prepare application documents through a connected
ChatGPT subscription. This page describes how the pieces fit together; the
[README](../README.md) covers setup and user-facing behavior.

## Processes

One application image runs every process. Compose starts each as its own
container (`docker/entrypoint.py --api` or `--worker <module>`); in local
development, run the same modules with `python -m hiring_scraper.app.<module>`.

| Process | Module | Responsibility |
| --- | --- | --- |
| Migrations | `--migrate` | Prepares the database role and applies Alembic migrations before anything else starts. |
| API | `app.api` | FastAPI HTTP API and the built React frontend. Reads cached data only; it never crawls or runs model inference inside a request. |
| Feed worker | `app.worker` | Leases due job feeds one at a time, fetches them, and saves new, changed and closed postings. Seeds the default RSS sources. |
| Discovery worker | `app.discovery_worker` | Runs search-area campaigns: finds employers from OpenStreetMap, resolves missing homepages, and discovers career pages and ATS feeds. |
| Search snapshot worker | `app.search_snapshot_worker` | Rebuilds materialized job-search snapshots so browsing does not wait for ranking. |
| Semantic search worker | `app.semantic_search_worker` | Hosts the pinned local multilingual E5 embedding model and indexes companies and jobs. |
| Profile match worker | `app.profile_match_worker` | Rebuilds stale profile matches using local rules and local semantic evidence. |
| Application preparation worker | `app.application_preparation_worker` | Drafts, reviews and revises application documents from the PostgreSQL queue. |

PostgreSQL (with PostGIS and pgvector) is the only database. Development and tests use
the same migrated schema and the same workers as production; requests never match,
prepare documents or build search snapshots inline.

## Data flow

```mermaid
flowchart LR
    Area[Search area: city/radius, state or Germany] --> Disc[Discovery worker]
    OSM[OpenStreetMap / Overpass] --> Disc
    Disc --> Sites[Homepage verification<br/>Wikidata, email domain, optional search]
    Sites --> Careers[Career page + ATS discovery]
    Careers --> Feeds[(Job feeds)]
    Manual[Job feeds page<br/>HTML, browser, RSS, ATS, BA] --> Feeds
    Feeds --> FeedWorker[Feed worker]
    FeedWorker --> Jobs[(Jobs + locations)]
    Jobs --> Semantic[Semantic search worker]
    Jobs --> Match[Profile match worker]
    Semantic --> Match
    Match --> Journey[Journey and job detail]
    Jobs --> Snap[Search snapshot worker] --> API[API + frontend]
```

## Code layout

- `hiring_scraper/` — source adapters and pure logic that does not depend on the database:
  - Fetching: `http.py` (bounded, paced, SSRF-checked requests), `crawl_policy.py`,
    `challenges.py` (bot/CAPTCHA detection, never solved), `browser.py` (headless Chromium).
  - Discovery: `location_discovery.py`, `website_discovery.py`, `osm_websites.py`,
    `discovery.py`, `pages.py`, `geography.py`.
  - Job extraction: `ats.py` (Greenhouse, Lever, Personio, Ashby, Recruitee,
    SmartRecruiters, RSS/Atom), `html_jobs.py`, `regional.py` (BA Jobsuche).
  - Matching: `matching.py`, `requirements.py`, `duty_evidence.py`,
    `task_alignment.py`, `semantic_matching.py` and `matching_v2/` (gates, feature extraction,
    weights, DEV-only fitter and calibration). `election_data.py` supplies optional city context.
- `hiring_scraper/app/` — persistence, API routers, workers and application preparation.
  `models.py` is the schema; changes ship as Alembic migrations in
  `app/migrations/versions/` (plus the rollback-safe step in `docker/entrypoint.py`,
  which `scripts/dev/migrate.py` and the test harness apply too). Write sites declare source changes through
  `source_changes.py`, which invalidates caches, queues rematching and publishes search generations. Routers live beside their domain: `journey.py`
  (stages, Journey list, momentum), `focus.py` (Today's focus), `profiles.py`,
  `companion.py`, `application_generation.py`; `api.py` keeps search, companies,
  feeds and discovery.
- `frontend/` — React + TypeScript + Vite single-page app.
- `extensions/application-companion/` — Chrome extension that fills application
  forms from a saved profile. It never submits a form.
- `runtime/codex/` — pinned Codex CLI bundled for ChatGPT subscription access.
- `fixtures/` — sanitized data for the seed command, CI smoke test and manual demos.

## Invariants

- **Closure only after complete scans.** A job closes only when a successful,
  complete scan omits it. Failed, blocked, partial, rolling or geographically
  filtered reads never close jobs. A reappearing job reopens the same record.
- **Untrusted URLs.** Source URLs are restricted to public HTTP(S) hosts; each
  redirect is rechecked, and response size, redirects, time and request counts are bounded.
- **No access-control evasion.** The scanner identifies itself, paces each origin,
  honors `Retry-After`, and stops on bot challenges or logins.
- **Separate company and job geography.** Companies match an area by their
  mapped establishment; jobs match by work location or explicit remote eligibility.
  City and state searches show these independently.
- **Provenance over guesses.** Homepages, employers and locations keep their source
  evidence; unknown values stay unknown. A shared domain alone does not merge employers.
- **Reads never infer.** API requests use cached matches; local embeddings and
  application drafting run in workers, outside database transactions, and publish only if inputs are unchanged.
- **Profile data stays private.** Application prompts use saved career facts, not inferred
  abilities; profile photos are never sent to the model.

## Concurrency model

Several processes write to the same PostgreSQL database. Keep these rules when adding writes:

- **Sources lock only source rows.** Feed, discovery and enrichment writers lock the job
  rows they change, in id order (`models.reserve_job_source_rows`), and never write
  `user_profiles`. The one exception is retiring jobs that have saved Journey stages: it
  holds the owning profiles `FOR SHARE` (through `before_publish`) while closing them.
- **Declare every source change.** After editing jobs, feeds, companies, profiles or
  vectors, call the matching `source_changes.*_changed()` function before
  commit. It drops caches whose inputs changed and queues rematching. Undeclared tracked
  writes fail tests (strict mode) and log an error in production.
- **Derived data is rebuilt from queues.** A declared job change is queued in `match_dirty_jobs`; companies and jobs needing vectors are queued
  in `semantic_search_refreshes` by database triggers. Workers drain these queues. Only profile-wide scoring inputs
  (career/matching preferences, `low_afd_vote_share_weight`, `search_area`, CV career facts, profile vectors, or matcher/semantic versions) mark a whole profile stale via `_profile_index_version`.
- **Presentation defaults avoid full rebuilds.** Changing only `matching_defaults.min_match_score` or
  `matching_defaults.include_unknown` does not change `_profile_index_version` or clear `profile_job_matches`; saving those
  fields calls `source_changes.profile_changed(..., presentation_only=True)` (bumping `source_version` to serialize against
  in-flight workers without dropping vectors or `match_index_version`) and runs
  `reevaluate_profile_new_applications`, which shares `application_match_allowed` with the
  workers, leaves user-decided stages untouched, and defers dirty/stale/legacy rows to `match_dirty_jobs` (`ON CONFLICT DO
  NOTHING`).
- **One match computation, single-snapshot reads, and version-stamped explanations.** Indexers (`job_search.py`) and
  read fallbacks (`search_snapshots.py`, `journey.py`) share `match_compute.compute_match` (local rules + local semantic evidence).
  Every `profile_job_matches` upsert stores a JSONB-safe (NUL/surrogate/non-finite stripped) `explanation` bounded to
  `<= 16 KiB` on the final `json.dumps(..., allow_nan=False)` wire bytes (including `requirement_observations` and
  `local_context.city_election_results`), stamped with internal provenance `_stamp` (`job` revision and `sem` hashes;
  stripped before returning) and `score_version` (`current_score_version()`) with
  `explanation['score'] == match_score`. Read paths validate `profile_job_matches`, `match_dirty_jobs`, `user_profiles`
  and `match_semantic_documents.source_hash`
  in ONE consistent SQL statement (`load_stored_match_snapshots`) for only the displayed `(profile_id, job_id)` pairs,
  loading vectors only on fallback. Indexed search treats a missing `profile_job_matches` row as an
  authoritative `profile_conflict` only when the profile index is current and the job is not dirty, and recomputes via
  `compute_match` otherwise. Journey keeps rank and card score consistent, local-only: stale/dirty rows with a faithful
  stored explanation render the same ranked stored card flagged `'refreshing': True` (with `matching_pending = True`);
  rows without a faithful stored explanation keep their stored rank order — rendering a neutral `'refreshing': True`
  card from the stored rank (or no score card when unranked) instead of recomputing or re-sorting on the displayed page,
  while the profile match worker supplies the local explanation.
- **Profiles are versioned, not locked by publishers.** Profile edits bump
  `user_profiles.source_version` (through `source_changes`). Publishers of per-profile data
  record the versions when they start, compute without locks, then call
  `models.hold_profiles(session, {id: version}, wait=False)` before their derived writes.
  It holds the rows `FOR SHARE` (publishers run side by side; edits wait) and raises
  `ProfileIndexChanged` if a version moved or an edit is in flight; the work is retried.
  The match worker passes expected versions.
- **Lock order is jobs → profiles → derived rows.** Lock affected job rows first
  (`reserve_job_source_rows`), then hold profiles, then write matches or Journey rows.
  Profile editors write the profile row before derived rows, so neither side
  can deadlock. Hold exclusively (`exclusive=True`) only to write the profile row itself.
- **Retry contention, don't discard work.** Wrap persistence of expensive results in
  `database.retry_transient()`, and log loop failures with `database.log_worker_failure()`
  (one warning line for contention, a traceback for bugs).
- **No unbounded periodic scans.** Poll loops must do O(batch) work when idle. Backfills
  use bounded id windows and stop once complete; the profile/job vector refresh checks the
  rematch queue and new ids first, then sweeps one id window at a time.
  `tests/test_postgres_idle_cost.py` measures every worker's idle cycle at two inventory
  sizes and fails if its cost grows; add new poll loops to it.

## Matcher rollout

`RADIUS_MATCHER=v1` is the default. Set `RADIUS_MATCHER=v2` to opt into the new scorer.
Use the same setting for the API and all workers. Both the stored
score version and the profile index version include the active matcher version,
so switching queues the usual background rebuild. Journey retains user-selected
stages and displays faithful stored cards while their scores refresh. V2 promotion
requires passing the target gates for both the overall and heldout splits of each
evaluation dataset.

V2 adds evidence and preference contributions into a raw score and maps it
monotonically onto 0–100 using DEV-fitted knots. Cached local semantic evidence
averages up to three duty or requirement chunks and accounts for each posting's
similarity distribution. Job semantic inputs keep technical evidence from mixed
clauses after language and work authorization text is removed. The match semantic
worker refreshes its versioned vectors, and rules remain available in the meantime.

Visible language and work authorization flags can deduct up to five points combined.
The matcher hides a job only when it can cite an explicit incompatibility in both
the posting and saved profile. Stored explanations include gate citations, the
contribution ledger and calibration within the same 16 KiB limit. When space is
tight, the projection shortens quotes while preserving contribution totals.

## Testing

Every database test runs on PostgreSQL through `tests/pg.py`: it migrates the database
in `TEST_POSTGRES_URL` once per run (real triggers, generated columns and indexes) and
empties every table before each test. `pg.isolated_engine()` gives migration and DDL
tests a private schema; `pg.run_workers()`, `pg.list_jobs()` and `pg.list_applications()`
drain the worker queues the way production does before a test reads results. Run the
suite with `scripts/dev/postgres-sandbox.sh` (a throwaway local cluster),
`scripts/ci/postgres-local.sh` (an existing local test server) or `scripts/ci/run.sh`. The extension and application browser suite in
`tests/application_companion/` needs `RADIUS_EXTENSION_BROWSER_TESTS=1` and runs as two
CI shards. Node checks for the frontend and extension are listed in `scripts/ci/check.sh`.
The `tests/frontend_*_browser.py` scripts are manual end-to-end checks against an isolated
preview started with `scripts/dev/preview.sh`; they read the preview URLs from
`APPLICATION_TEST_WEB_URL` and `APPLICATION_TEST_API_URL`. `tests/test_postgres_*.py`
cover concurrency, lock ordering and idle worker cost. See the [CI guide](ci.md).
