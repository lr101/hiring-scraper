# Code review: robustness, testing and architecture (November 2026)

## Goal

Radius exists to **find jobs, match them to a profile, and make applying easier,
stress-free and gamified**. Every recommendation below is judged against that loop:

| Stage | What "working" means |
| --- | --- |
| Find | Fresh public inventory; crawled work is never thrown away because of a transient database hiccup. |
| Match | Rankings stay current within minutes of a source change, without blocking ingestion or the UI. |
| Apply | Journey, documents and autofill respond instantly; progress (weekly goal, momentum, stages) is visible and rewarding. |

## Production errors investigated

### 1. Semantic search indexer hits the statement timeout

**Symptom:** `semantic-search-worker` logged `QueryCanceled: canceling statement due to
statement timeout` for `SELECT jobs.id ... WHERE NOT EXISTS (semantic_search_documents)
AND NOT EXISTS (semantic_search_refreshes) ORDER BY jobs.id LIMIT 16`.

**Root cause:** the backfill scan ran on *every idle poll* (every 2 s), forever. It walked
the whole `jobs` table from id 1 with two anti-joins, even after every row was indexed.
Inserts and edits are already queued by database triggers, so the scan was only ever
needed once per model revision. Feed scans update `last_seen_at` constantly, so the
"index-only" scan also has to fetch heap pages. Reproduced with 300k jobs after update
churn: one empty poll touched about 1.5 million buffers (680k heap fetches). Production
has more rows and a colder cache, so it crossed the 120 s timeout.

**Fix:** once `semantic_search_index_state.backfill_complete` is set for the current model,
the scan is skipped. During a backfill the scan walks bounded id windows with a cursor
that only advances after vectors are saved, so a failed embedding batch cannot skip rows.
Idle poll: from O(inventory) to about 14 ms. Tests: `tests/test_postgres_semantic_backfill.py`.

### 2. Lock timeouts on `user_profiles`

**Symptom:** `LockNotAvailable ... while locking tuple in relation "user_profiles"` in the
profile match worker, the discovery worker and others. Discovery threw away a completed
crawl (12 pages, 800 jobs) and re-crawled later.

**Root causes** (three that compounded):

1. **Every source write locked and wrote profile rows.** A flush-time listener ran
   `UPDATE user_profiles SET match_index_version = NULL` for every job that had a
   cached AI assessment, and every saved assessment did the same. With one profile,
   that row became a global mutex shared by the feed worker, the discovery worker,
   enrichment, AI publication and match publication. The upfront "owner reservation"
   (`SELECT ... FOR UPDATE` on profiles in every feed and discovery transaction) existed
   only to give that `UPDATE` a deadlock-free lock order.
2. **Each invalidation forced a full rebuild.** A `NULL` index version marks the whole
   profile stale, so the match worker re-matched the entire inventory after any single job
   or assessment change. With feeds every few seconds and 25 parallel AI assessments, it
   was almost always rebuilding, and taking the profile lock for every page it published.
   The AI publisher already contained a workaround that restored the version after its own write.
3. **`FOR UPDATE` conflicts with foreign-key checks.** Inserting any row that references a
   profile (match, application, AI task, assessment) takes `KEY SHARE` on it. `FOR UPDATE`
   waits for all of those transactions; `FOR NO KEY UPDATE` does not. Job rows already
   used the compatible mode; profiles did not.

**Fix:**

- Source writers lock **job rows only**. A changed job already enters
  `match_dirty_jobs`, which rematches that one job for every profile. Profile-wide
  invalidation is kept for genuinely profile-wide changes (preferences, CV career facts,
  profile vectors).
- Profile locks became FK-compatible, and were later replaced by version-checked
  publication (`models.hold_profiles()`, see the roadmap below).
- Feed and discovery persistence retry lock timeouts and deadlocks through
  `database.retry_transient()` instead of discarding fetched results.
- Expected contention logs one warning line (`database.log_worker_failure`); unexpected
  errors keep the full traceback.

Tests: the PostgreSQL concurrency suite now asserts that ingestion takes no profile locks
and that a profile publication does not block FK inserts. The second test reproduces the
production error when the hold is switched back to `FOR UPDATE`.

### 3. Also fixed during the review

- **Semantic job search on PostgreSQL raised `NameError`.** The model key constant was left
  in `api.py` when search moved to `search_snapshots.py`. It now has one definition in
  `semantic_matching.py`.
- **AI queue reconciliation held one transaction across the whole inventory every minute.**
  It now commits in chunks of 500 and reloads task rows per chunk, so claimers don't queue
  behind it.

## Structural observations

| Area | Observation | Risk |
| --- | --- | --- |
| Consistency model | Derived data (matches, Journey "New" rows, snapshots, vectors) was kept consistent with **pessimistic cross-table locks** ordered by hand (job → profile, advisory locks, savepoints). Correctness depended on every writer following comments like "lock X before Y". | High: one missed lock order became a production deadlock or timeout. Partly addressed: ingestion no longer participates. |
| Hidden side effects | Eight SQLAlchemy `before_flush`/`after_flush` listeners in `models.py` took locks, deleted caches and enqueued work on *any* flush. | Fixed: write sites declare changes through `source_changes`; a detector fails tests on undeclared writes. |
| Two databases, two code paths | 60+ `IS_SQLITE` / dialect branches and a ~300-line SQLite schema upgrader beside Alembic. | Fixed: PostgreSQL only; every test runs on it. |
| PostgreSQL tests not in CI | `test_postgres_*.py` only ran when an agent remembered `postgres-local.sh`. | Fixed: container CI now runs them against the deployed database image. |
| Periodic full scans | The indexer, the AI reconciliation and the profile backfill each scan the inventory from a poll loop. | Medium: the indexer is fixed and the AI sync is chunked; the reconciliation is still O(inventory) CPU per minute. |
| Module size | `api.py` (2.2k lines), `matching.py` (1.9k), `discovery_worker.py` (1.5k), `search_snapshots.py` (1.2k). | Medium: slows review and makes ownership unclear. |
| Logging | Contention logged as ERROR with tracebacks hid real bugs. | Fixed for the worker loops. |

## Local testing options

| Option | Fidelity | Effort per run | Verdict |
| --- | --- | --- | --- |
| Former SQLite unit suite | Low for locking, triggers, pgvector, queues | Seconds | Removed: the whole suite now runs on PostgreSQL through `tests/pg.py` in about three minutes. |
| **`scripts/dev/postgres-sandbox.sh`** (new): throwaway PostgreSQL cluster, no root, no Docker | High (real PG 17, PostGIS, pgvector, migrations) | About 1 min | **Recommended default before any change to workers, locking, queues or migrations.** Needs server binaries with PostGIS and pgvector installed. |
| `scripts/ci/postgres-local.sh` against an existing server | High | Requires a configured server | Kept; the sandbox wraps it. |
| `scripts/ci/run.sh` (Docker) | Highest: production images, smoke test, and now the PostgreSQL suite against the db image | Several minutes | Run before pushing. |
| Volume reproduction (seed 100k+ jobs, `EXPLAIN (ANALYZE, BUFFERS)`) | Exposes O(n) behavior | Manual | **Recommended next:** add a seeded performance smoke test that fails when an idle worker cycle touches more than N buffers. |
| Testcontainers / pytest fixtures | High | Adds dependencies | Not needed now; the sandbox covers it without new packages. |

## Architecture options

### A. Keep pessimistic locking, tighten discipline (status quo plus this review)

Smallest change, already applied: ingestion locks only job rows, profiles use FK-compatible
locks, writers retry. Remaining risk: listeners and hand-ordered locks still guard
match publication.

### B. Event-driven derived data with optimistic publication (recommended direction)

Treat everything derived (matches, Journey "New", AI tasks, search snapshots, vectors) as
**projections** rebuilt from queues:

1. Source writers only write source tables and append a dirty marker (already true for
   `match_dirty_jobs`, `semantic_search_refreshes` and `job_search_refreshes`).
2. Workers compute outside transactions, then publish with a **version check**
   (`UPDATE ... WHERE source_hash = :seen`) instead of holding locks across reads.
   A conflict simply re-queues the item.
3. Replace flush listeners with explicit calls at the few write sites (`mark_jobs_dirty(session, ids)`).
4. The UI reads projections plus a "pending" count, which Journey already shows.

Benefits: no cross-process lock ordering, no deadlocks, horizontal workers, identical
semantics on SQLite (drain inline) and PostgreSQL (drain in a worker).
Cost: incremental refactor of `job_search._refresh_application_matches_for_jobs`,
`profile_llm` publication and the listeners in `models.py`, guarded by the PostgreSQL suite.

### C. PostgreSQL everywhere

Drop the SQLite runtime path and use the sandbox or Compose for development. This removes
the 60+ dialect branches and the hand-written SQLite migrator, so tests exercise the
production engine. Cost: contributors need PostgreSQL locally (the sandbox or Docker).
**Recommended together with B**, since B makes the SQLite inline path cheap during the transition.

### D. External queue (Redis, RabbitMQ)

Not recommended yet. The database-backed queues are adequate at single-user scale.
The problems were locking and unbounded scans, not queue throughput.

## Roadmap and status

| # | Recommendation | Status |
| --- | --- | --- |
| 1 | Indexer backfill, profile-lock removal from ingestion, FK-compatible locks, persistence retries, quieter logs, chunked AI sync, PostgreSQL tests in CI, PostgreSQL sandbox, semantic search `NameError` | Done |
| 2 | Volume performance smoke test | Done: `tests/test_postgres_idle_cost.py` measures every idle worker cycle (via `EXPLAIN ANALYZE` buffers) at 3k and 40k jobs. It found a second unbounded loop in the profile/job vector refresh, now bounded. |
| 3 | Replace flush listeners with explicit calls | Done: see below. |
| 4 | Version-checked publication instead of profile locks | Done: see below. |
| 5a | Split `api.py` into domain routers | Started: Journey and Today's focus have their own routers. Search, feeds and discovery remain. |
| 5b | Retire the SQLite runtime | Done: PostgreSQL only. Local development uses `compose.dev.yaml` or the sandbox; tests use `tests/pg.py`. |
| 6 | Product: daily next steps | Done: **Today's focus** on Journey (up to three roles, "x of 3 sent today"). "Prepare all above X" stays dropped: auto-preparation already prepares the top New roles. |

### Explicit source changes (item 3)

Write sites call `source_changes.jobs_changed`, `feeds_changed`, `companies_changed`,
`profile_changed`, `assessments_changed` or `vectors_changed`. Each call drops the caches
whose inputs changed (by comparing stored hashes with values recomputed from the
database), queues rematching, and marks the transaction so one search generation is
published at commit. The "can't forget" guarantee the listeners gave is kept by a
detector: it records tracked column changes on every flush and, at commit, reports any
that were not declared. Tests run in strict mode (`RADIUS_STRICT_SOURCE_CHANGES=1`, set by
`tests/pg.py`) and fail; production logs an error and carries on.

### Version-checked publication (item 4)

`user_profiles.source_version` (migration `0041_profile_source_version`) is bumped by
every declared profile input change. Publishers read it when they start computing and
call `models.hold_profiles()` with the expected versions after computing, before their
derived writes:

- `FOR SHARE`, so publishers (match worker, AI publication, duplicate consolidation,
  Journey stage saves) no longer exclude each other; only a profile edit does.
- Worker publications pass `wait=False`: an in-flight edit defers the page
  (`ProfileIndexChanged`, retried) instead of queueing behind it and timing out.
- A changed version raises `ProfileIndexChanged` and the work is retried with fresh inputs.
- Holding before derived writes keeps the profile → derived order editors use, so an
  edit and a publication cannot deadlock. The match worker holds exclusively only when it
  also marks the profile index current.

Editors still wait for an in-flight publication of the same profile, which is bounded
by one page of derived writes.

### Next

- Continue splitting `api.py`: search/browse, feeds and discovery routers.
- Make the AI reconciliation incremental (driven by `match_dirty_jobs`) rather than a
  per-minute inventory pass. It is chunked now, but still O(inventory) CPU.
- Product: let users set the daily focus size (default 3) and show a gentle weekly
  summary, without daily streak pressure.
