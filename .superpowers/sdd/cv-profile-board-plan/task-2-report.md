# Task 2 report: bounded regional source adapter

Implemented the observed BA public search/detail adapter in `hiring_scraper/regional.py` and an explicit acquisition/import CLI in `hiring_scraper/app/regional.py`. No existing HTTP, API, matching, frontend, model, worker, or seed code was edited.

The adapter validates the fixed HTTPS host and observed search/detail paths, uses the captured public client header, gives every request/retry a shared budget, and preserves separate immutable HTTP captures per attempt. Acquisition requires a fresh directory and saves query terms, page totals, truncation/errors, request attempts, raw summaries/details, and a reusable normalized snapshot. Role terms default to broad English/German project/product/process/training/program/design families. Round-robin detail selection prevents the first role family consuming the detail allowance. Any HTTP failure, malformed search, page/detail cap or exhausted budget marks the result partial. Even successful selected searches are explicitly identified as selected searches, never a complete market scan.

`normalize_job(row, observed_at=None)` and `parse_search(payload)` support observed `ergebnisliste` and legacy `stellenangebote` shapes. Coordinates are paired, finite, and range checked. Full description newlines, stable BA reference/URL, actual external source URL, timestamp, original source record, salary amounts/unit evidence, publication expiry versus contract end, employer hash and agency flags are preserved. Only an explicit 100% home-office percentage yields fully remote. Possible/negotiated home office remains hybrid; office country never invents remote permission. Explicit `remote_country_codes` require `remote_scope_source` evidence.

`import_snapshot(session, snapshot_or_path, observed_at=None)` accepts the captured raw detail list or `{'jobs': normalized_rows}`, returns company/feed/job creation/update/skip counts, and leaves transaction ownership to the caller. Identity uses employer hash with normalized-name fallback plus reference-scoped sparse-refresh recovery. It never merges conflicting public hashes. Failed/sparse refreshes preserve earlier richer text and source evidence. Missing jobs never close, missing-scan counters are untouched, and imported feeds always have `next_scan_at=None`, excluding them from ordinary ATS complete-feed lifecycle.

Repeatable commands:

```sh
.venv/bin/python -m hiring_scraper.app.regional import /tmp/hiring-cv-review/ba-combined-details.json
.venv/bin/python -m hiring_scraper.app.regional acquire --city Heidelberg --radius-km 35 --term Projektkoordinator --term 'Product Operations' --out /tmp/ba-fresh-run --max-pages 2 --max-details 50 --max-requests 80 --delay 1 --import
.venv/bin/python -m hiring_scraper.app.regional acquire --profile-id 1 --out /tmp/ba-fresh-profile-run --max-requests 100
.venv/bin/python -m hiring_scraper.app.regional acquire --profile-json /path/to/saved-profile-or-area.json --out /tmp/ba-fresh-config-run
```

Saved searches read `UserProfile.preferences.search_area` or JSON `preferences.search_area` / `search_area` / a direct area object. Acquisition uses the saved city (or place label) and radius. A coordinate-only area needs an explicit `--city`: the supplied observed acquisition contract only established the `wo` city/place parameter, so no speculative coordinate-query parameter was introduced. Coordinate fields remain available to application board scope/matching through the profile. No new live acquisition was performed; the captured954 rows were used for verification.

Evidence:

- Test-first module failures observed before implementation; targeted regression failures caught sparse agency-evidence loss, the observed `OESTERREICH` country spelling, extra detail path segments, and sparse hash omission. Each was fixed and rerun green.
- `.venv/bin/python -m unittest discover -s tests -p 'test_regional*.py'`: 18 tests pass. Covers both search shapes, pagination and caps, partial/malformed responses, malformed coordinates, immutable retry captures, fixed-host/path rejection, request budget, reference mismatch, newlines/full source preservation, explicit remote versus hybrid, idempotent raw/normalized imports, salary/agency evidence, saved area, employer identity and no closure/scheduling from incomplete search.
- Existing regression tests: HTTP 6 pass, ATS 19 pass, seed 5 pass.
- Compileall succeeds for both modules and both test files; both CLI help commands succeed.
- Captured954 import into temporary in-memory SQLite: 954 jobs,468 employers/feeds,954 full descriptions,170 agency rows,160 salaries,1 explicit remote and112 hybrid. Repeat import creates0 companies/feeds/jobs (954 updates). Scheduled regional feeds:0. No production database was changed by this smoke verification.

Callable interfaces are available for Task 4's explicitly invoked demonstration seed helper. Normal feedworker integration was unnecessary because `next_scan_at=None` already excludes these feeds.
