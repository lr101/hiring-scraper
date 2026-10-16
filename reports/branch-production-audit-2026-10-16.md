# Branch production audit — 2026-10-16

Audited `t3/f007232d` at `230ea72a`, plus the fixes in this working tree, against freshly fetched `origin/main` (`fce55847`). No production systems were accessed. No merge, push, or deployment was performed.

## Assessment

The branch contains current main and Git can merge it without conflicts. The PostgreSQL migration graph has one merged head, `0027_merge_journey_and_main`. A populated upgrade from main succeeded. Deployment readiness remains conditional: image-only rollback fails, multiple replicas require an explicit connection budget and serialized migrations, and broad job search/profile matching still has significant cold-start costs at 200,000 jobs. Company count alone is insufficient to size this application; active jobs, selected profiles, search areas and simultaneous requests matter too.

## Fixed in this audit

- **Cancelled campaign lock inversion.** `_finish_campaign_company` previously locked its task and parent, then implicitly locked Company while clearing its lease. Crawl persistence takes Company before its parent, creating a cycle. The finalizer now locks Company first. A real PostgreSQL regression reproduced the original lock timeout and passes with the fix.
- **Stale cancellation clears a newer lease.** An expired cancelled crawler could clear the lease assigned to a newer standalone crawl and change the old failed run to cancelled. The cancellation finalizer now verifies the latest active run before touching the company lease or run. Stale callers finish only their own campaign item. A PostgreSQL regression failed before the guard and passes afterward.
- **Repeated geography normalization.** Country/city checks repeatedly normalized identical alias strings for every job. Profiling 100 repeated country/city checks measured 167,100 Unicode normalizations before the fix and 228 afterward. Bounded process-local caches now share immutable normalized strings and alias tuples. This preserves scope decisions and avoids shared mutable cache values; existing geographic regressions cover spellings and ambiguous cities.

## Migration and release findings

The disposable PostgreSQL database contained 200,000 companies, 200,000 feeds, 200,000 active jobs and 100 existing application rows at main's migration head. Upgrading applied only the journey migrations and merge revision, preserved the application rows, and backfilled 100 legacy applied events, 100 interview events and 25 accepted events for the seeded statuses. A second upgrade was a no-op. The existing collation repair did not run again on this path. A fresh database upgrade was also exercised.

**Release blocker unless a rollback plan is accepted:** the old main migration runner fails after this upgrade with `Can't locate revision identified by '0027_merge_journey_and_main'`. The old image automatically runs Alembic on startup, so reverting the image tag alone will not restore service. Prepare a database backup/restore rollback, or an explicitly tested branch-aware rollback procedure using the new image before restoring the old image. Journey event tables contain new user history and must be exported if a schema rollback must preserve it. Merely downgrading to the collation parent was tested: it removes the merge marker but leaves both branch heads recorded, so it is insufficient.

**Topology decision required:** entrypoint bootstrapping does not serialize the complete `alembic upgrade head` across replicas. Simultaneous fresh-version startups can race on migration DDL. For this release, run migrations exactly once and serialize app startup; decide whether deployments need a dedicated migration job or a database advisory lock around the complete migration runner. The role-creation lock does not cover Alembic.

**Connection budget required for multiple replicas:** the entrypoint defaults to 40 API connections plus 10 for each of four background worker processes: up to 80 connections per app container. Two containers can exceed PostgreSQL's default 100 connections, excluding administration/migration clients. Compose's `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` overrides apply to every process. Budget the sum across all replicas against the actual server limit, with headroom. The existing real PostgreSQL pool/timeout regressions pass.

If production is behind current main's collation migration, its first upgrade also runs database-wide `REINDEX DATABASE CONCURRENTLY` before the app starts serving. Its duration and temporary disk needs depend on production indexes; this was not benchmarked on production data. Confirm the deployed revision and image/extension versions before rollout.

## Search and threading findings needing a design decision

- **Synchronous profile rebuilds:** `/api/v1/applications` calls `ensure_profile_match_indexes` inside the HTTP request. A stale/new profile scans every active job, computes matches, and holds its profile lock and database transaction for the entire rebuild. Existing fresh-index checks avoid that lock, but the initial rebuild remains expensive. Concurrent source finalizers can wait for the same profile. SQL statement timeouts do not bound the whole Python request or transaction. Move rebuilds to leased background work with progress, or accept and provision for the first-request latency; this needs an explicit product/API decision.
- **Cold broad browse:** a Germany/state snapshot scans eligible jobs for exact geography and deduplication and stores one row per canonical winner. ORM batches are bounded, but candidate IDs, deduplication keys and scopes grow with the result population. The dedicated snapshot worker processes one queued search at a time, so an expensive country refresh delays other queued searches. The API returns `search_mode=warming` when no snapshot is available. Decide acceptable warm-up latency and whether broad scopes need incremental/shared materialization or separate worker capacity.
- **Global invalidation and churn:** source changes advance a shared search generation. Hot searches can use recent stale snapshots for up to one freshness bucket (15 minutes), while rebuilding in the background. Sustained crawling and many distinct saved areas can keep the worker busy and increase stored snapshot/result churn. The delayed invalidation marker now avoids holding the shared generation lock through domain writes, and the concurrency regressions verify this ordering. It still serializes source commits briefly.
- **Timeout and lease boundaries:** database statement timeout defaults to 120 seconds; discovery/scan work uses expiring leases and the snapshot queue uses a 15-minute lease. Long Python computation can exceed a statement timeout because it is not a single SQL statement. More worker processes do not automatically make such CPU-bound work faster. Size leases, process count and database pool capacity together after measuring realistic jobs and profiles.
- **Semantic search remains unqualified by this benchmark.** The local PostGIS/vector extensions were installed, but no resident ONNX model or production-like vector corpus was loaded. Tests exercised the keyword/browse fallback and connection release around embedding waits. Benchmark actual vector search, filtered recall, model warm-up and indexing backlog on staging before calling semantic behavior proven at this scale.

## Validation

See the final audit response for completed test counts and large-data timings. The synthetic benchmark is a local diagnostic, not a production SLA: all companies are German, each has one feed and one job, descriptions are short, one profile has empty preferences, and clients call the real endpoint functions directly rather than traversing HTTP/TLS. The first radius fixture happened to contain no companies inside the selected radius and is not a meaningful populated radius-load measurement.

The preview adapter provides an isolated SQLite fixture for manual inspection; it does not validate PostgreSQL migration or capacity behavior. Docker is unavailable in this environment, so the complete production image/Compose smoke pipeline was not run.

Completed checks:

- Full Python suite: 647 tests discovered, 631 passed and 16 PostgreSQL-only cases skipped in the default run; all 16 passed separately against an empty disposable PostgreSQL database. Two of those are new concurrency regressions.
- Focused discovery/campaign/worker-recovery suite: 58 passed.
- Frontend production build, application request tests, profile/location defaults tests, Python compilation, release-note validation and `git diff --check` passed.
- Independent read-only review found no remaining blocker in the audit's code changes.
- Local main-to-branch populated migration and repeated upgrade passed. Running old main's migration environment afterward reproduced the unknown-revision rollback failure.

Local synthetic scale measurements (200,000 companies / feeds / active jobs):

- National company browse: 0.141 seconds; company keyword search: 0.134 seconds (1,111 matches).
- Original country snapshot build was stopped after more than 210 seconds without completing. After the normalization fix, a country snapshot completed in 97.804 seconds; the benchmark process peaked at approximately 397 MiB RSS. The original run did not finish, so this is a lower-bound comparison rather than a precise speedup measurement.
- Warm country job page: 0.601 seconds with 200,000 results. Thirty-two warm searches on 16 threads completed in 9.36 seconds, all in `browse` mode; there were no query/pool failures. This direct-function benchmark used the standalone engine's default six-connection pool, not the Compose API process's 40-connection budget.
- The synthetic profile index rebuild remained a long-running synchronous request after the normalization fix; see the limitation above. Empty profile preferences make this a simple fixture, not an upper bound for matching real CVs.

Suggested release sequence: verify the actual production revision and database connection limit; agree on rollback and acceptable cold-search/profile latency; take a recoverable database backup; migrate once with the new image while serializing startup; start one app replica within the connection budget; observe readiness, worker recovery and search warm-up before increasing traffic or replicas. Test the production images, semantic corpus and HTTP timeout settings on staging first.

The post-fix profile-rebuild diagnostic was stopped after more than 180 seconds without completing; its transaction rolled back. The country snapshot and migration diagnostics completed. These were separate local benchmark processes; no production job or worker was interrupted.
