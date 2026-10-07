# Full Karlsruhe OSM career discovery

**Observed:** 2026-10-05 UTC · **Scope:** dated OSM 15 km candidate snapshot · **Merged run:** [`run-29`](../data/career-poc/run-29/)

## Results

The complete snapshot contains **2,251 mapped candidate records**. Of these, **1,606 had a valid website URL and were crawled**; **645 have no OSM website tag** and could not enter the homepage crawl. The 1,606 candidates resolve to 1,556 unique homepage URLs and 1,506 hosts. A mapped record is an establishment or organization candidate, not proof of a distinct legal employer; branches, public bodies, nonprofits and other organizations remain in the source set.

The crawler recorded exactly one result for each website-bearing OSM source ID. The merged run combines three earlier slices (504 candidates) and a four-worker final slice (1,102 candidates). Across the complete pass it validated **9,865 unique HTTP captures** and checked each saved body against its recorded SHA-256 hash. The crawler used a six-page, depth-three bound, a 12-second request timeout, per-origin pacing and a 16,000-live-request budget per slice. The full observation window was 22:05–23:47 UTC.

| Current crawl outcome | Candidate records | Interpretation |
|---|---:|---|
| Career content found | 477 | Career/job page matched the content heuristics; a feed was not required. |
| Jobs extracted | 141 | An HTML job source passed the employer-site or branded-site provenance check. |
| Jobs feed found | 42 | A supported structured/API feed parsed, including feeds that can be empty. |
| ATS identified | 7 | A recruitment platform was detected, but no supported feed parsed. |
| Unresolved | 939 | No confirmed page or supported feed within the crawl limits, or an access/technical issue. This does not prove that no careers site exists. |

Thus **667/1,606 (41.5%)** produced a positive page/feed/provider signal: 660 career-page or parsed-source results and 7 detection-only ATS leads. The 183 parsable sources came from 141 HTML pages plus 42 structured feeds: 32 Personio, 2 Lever, 5 Schema.org DataFeed, and 3 Greenhouse. They contained **1,655 accepted posting rows**: 564 HTML, 423 Personio, 200 Lever, 290 Schema.org, and 178 Greenhouse. These are source rows, not globally deduplicated vacancies; the UI deduplicates known Greenhouse requisition variants. OSM branch rows can also represent the same employer more than once.

Posting metadata is uneven. Among those 1,655 source rows, 1,524 had descriptions, 1,181 had a location string, 269 included a structured locations array, 1,279 had an employment type, 954 a posted date, 793 a department, 422 each a schedule and seniority value, 57 a remote flag, and 50 salary data. Missing fields remain unknown; the crawler does not infer them. Location values still require normalization before a radius match is precise.

## Quality and access findings

An earlier HTML heuristic accepted **336 rows from 66 external pages** without adequate evidence that the pages represented the employer. The full-run filter removed all 336 from the app data. The largest source was generic HOGAPAGE categories (142 rows); other pages included unrelated job portals and vendor directories. Direct employer-hosted pages and externally hosted pages carrying a distinctive employer brand and linked from the employer site remain eligible. The filter retained 564 of 900 discovered HTML posting rows.

The merged captures contain 5,906 HTTP 200 responses, 2,088 404s, 1,544 redirects, 229 network errors, and 38 HTTP 403s; the remainder are smaller groups of 3xx, 4xx and 5xx results. A failed or blocked homepage, robots request or feed is not evidence that an employer has no jobs. No login, challenge or access control was bypassed. Six-page limits, language variants, JavaScript-rendered boards, incomplete company names, and unrelated OSM records all constrain recall and precision.

The positive count is a discovery yield, not a measured precision score: the whole set was not manually labeled. A verified page/feed means the evidence and parsing rules passed; it does not verify that every extracted row is current, local, or legally attributable to a separate employer. Feeds are dynamic and the counts are a dated observation.

## Repeatable app and scheduled scans

`data/career-poc/run-29/` contains the merged seeds, results, company CSV, summary and source manifest. Per-request capture indexes and response bodies stay local and are not committed. `fixtures/karlsruhe-career-enrichment.json` was refreshed atomically from that run while preserving prior manually verified associations and positive evidence. Run the deterministic merge again with:

```bash
mise exec -- uv run python experiments/finalize_karlsruhe_discovery.py
```

It refuses to overwrite an existing output run, rejects missing or duplicate source IDs, verifies all capture hashes, excludes unverified external HTML rows, and only then updates the app fixture. The normal seed import is safe to repeat and preserves newer worker-discovered state.

A fresh SQLite migration and two imports of that fixture returned the same totals each time: **2,251 companies, 186 feeds and 1,678 stored job records**. The database total includes three previously verified feeds and 23 job records retained from the earlier manual Mail & Media association in addition to the current crawl's 183 parsed sources and 1,655 rows. At the default Karlsruhe center and 15 km radius, 2,249 candidate records and 1,602 domain-bearing companies match; two mapped objects fall just beyond the exact circle. The location-or-remote job query returns 294 active results: 253 with an in-area work location and 41 explicitly remote.

The deployment path now has separate `discovery-worker` and job-feed `worker` services. The discovery worker leases one due company at a time, uses the same bounded robots-aware crawler, stores run evidence and parsed jobs, retries failures, and schedules homepage rediscovery every 30 days by default. Discovered feeds are then scanned every six hours; incomplete results do not close jobs. Both intervals and scan budgets are configurable in Compose. The shared API/worker image and frontend image have a GitHub Actions build-and-publish workflow.

Local verification covered the full test suite, the production frontend build, fresh SQLite migrations, repeat fixture import, and location/remote API queries. Docker/Compose is not installed in this environment, so image builds and a fresh PostGIS Compose startup still need to be exercised on a Docker host. Nationwide OSM import, domain enrichment for the 645 website-less records, and support for additional ATS feeds remain future work.
