# Hiring scraper implementation plan

## Global constraints

- The service is private, self-hosted, English-only, and intended for a desktop browser.
- Accounts have no authentication. Shared company and job data must not expose one account's
  profiles, matches, notes, exclusions, or workflow state to another account.
- Germany is the only country. A profile accepts several cities and a radius for each city.
- Fully remote Germany jobs match any remote-enabled profile. Hybrid and onsite jobs must have a
  location within a configured city radius. Unknown locations remain visible with a score penalty.
- Matching is deterministic. Use hard filters and weighted title and skill ranking. Do not use an
  LLM, CV upload, notifications, exports, or a public API.
- Do not block or penalize recruiters by default. Users may create explicit exclusion rules.
- Retain closed jobs. Mark a job closed only after two successful source runs miss it.
- A job may match several profiles. A new job or newly matching existing job appears as New.
- Opening a job marks it seen for the selected account. Ignored reposts remain ignored by durable
  fingerprint.
- Fetch each source daily with a configurable low request rate. Stop and mark a source blocked when
  bot protection appears. Do not implement proxy rotation or CAPTCHA solving.
- The initial companies are Siemens, Bosch, SAP, Deutsche Telekom, and DHL.
- Docker deployment files target an external Docker-capable host. Never install or start Docker in
  this development container.
- Use test-first development and make a separate Git commit for each completed task.

## Task 1: Location and matching engine

Implement text normalization, conservative German and English title and skill synonyms, Haversine
distance, remote and hybrid location rules, hard filters, weighted scores, explanations, and
creation or update of one JobMatch per job and profile. Add focused tests for each rule.

## Task 2: Collection pipeline and source health

Implement the common collector contract, raw job record, adapter registry, HTTP block detection,
upsert lifecycle, two-miss closure, ignored repost propagation, CrawlRun accounting, daily Celery
task, and management command. Add fixture-based tests without external HTTP calls.

## Task 3: Five seed company collectors

Inspect Siemens, Bosch, SAP, Deutsche Telekom, and DHL career sites. Implement the smallest set of
reusable or custom adapters required to collect German jobs from all five. Add representative saved
response fixtures and parser tests. Add a data migration or command that seeds each career website
as its own Company and CareerSource record.

## Task 4: Profile and exclusion interface

Build structured create, edit, list, and delete views for profiles, multiple city-radius entries,
common metadata filters, scoring weights, and explicit exclusion rules. Scope all data to the
selected account. Re-evaluate open jobs after a saved profile changes.

## Task 5: Jobs, companies, and run interface

Build New, Jobs, Job details, Companies, Company details, Sources, and Runs pages. Support filters,
job workflow state, notes, seen-on-open behavior, source enable or unblock controls, and a manual run
action. Keep account selection on every page.

## Task 6: Deployment, operations, and final verification

Complete Docker deployment configuration, health checks, initial account setup, static assets,
backup and restore instructions, request-rate settings, and production settings. Run all local
checks, inspect migrations, verify command-line collection with fixtures, and obtain final review.

