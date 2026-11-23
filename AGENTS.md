# Project goal

Radius helps one person **find jobs, match them to their profile, and make applying
easier, stress-free and gamified**. Judge changes against that loop:

- **Find:** fresh, complete public job inventory; never lose crawled work to transient errors.
- **Match:** explainable, evidence-based ranking that stays current without blocking ingestion.
- **Apply:** fewer steps and less anxiety; reward progress (Journey stages, weekly goal,
  momentum); never submit an application on the user's behalf.

Prefer simple, robust designs over clever locking. Background work must degrade
gracefully (retry, queue, resume) rather than fail loudly. See `docs/architecture.md`
for the concurrency model and `docs/code-review.md` for known risks and the roadmap.

# Local verification

- During development, run focused tests and inexpensive checks relevant to the change. Do not run full quality, browser shard, PostgreSQL regression, or container suites until immediately before a requested GitHub push.
- Before pushing, follow the host verification commands in `docs/ci.md`: quality checks, frontend build, both browser shards, and the disposable PostgreSQL checks. Run `scripts/ci/run.sh` too when Docker is available. Report any checks that could not run and why; do not claim host checks cover image, container security, or PostgreSQL smoke behavior.
- PostgreSQL checks create and drop a uniquely named database. Use only a local test server and credentials authorized to create and drop databases, never a production server. The migration check needs PostGIS and pgvector. Follow the guide for connection settings and cleanup.
- Install missing tools or dependencies using the repository's pinned versions and lockfiles where practical. Never print database credentials or read private environment files into logs.
