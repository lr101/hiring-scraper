# Changelog

Notable changes to Radius are recorded here. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The application
version lives in `pyproject.toml`; the private frontend package has its own version.

This changelog starts with the repository maintenance setup. Earlier development
is available in the [commit history](https://github.com/lr101/hiring-scraper/commits/main/);
no historical releases have been reconstructed.

## [Unreleased]

### Added

- Application journey progress, weekly goals, and milestone rewards, with existing application history preserved as legacy milestones.

- Germany discovery across all 16 states with durable section progress and employer-scoped directory results, including all work locations.
- Failed Germany map searches can resume from their last saved section instead of restarting discovery.
- Continuous ranked career and feed checks: daily for large employers or recent jobs, every 3/7/30 days for other employers, with evidence-based pauses that fresh size or hiring evidence can reactivate. Company details expose cadence, reason, and next check.

- Whole-state company discovery for all 16 German states, with saved searches, recurring source refreshes, and browsing of those employers’ active postings across all work locations. Existing radius searches are preserved by the new state-search migration.

- Per-profile application tracking with New, Open, Not interested, Waiting for reply, Interview, Rejected, and Accepted statuses, plus a status-filtered application overview that retains closed and expired postings. PostgreSQL deployments apply the new job application migration during startup.
- Mobile result cards, bottom navigation, compact status filters, and larger touch controls for applications, job browsing, profiles, and search areas.
- Documented public Recruitee XML feeds with employer provenance checks and safeguards for active legacy HTML jobs.
- Missing company homepages can be discovered with Tavily basic search, bounded query fallbacks, and current-owner verification; regional search hints support listings without city or postcode.
- Contributor guide, community conduct policy, security reporting guidance, and structured bug and feature request forms.
- Changelog validation and GitHub Releases using curated notes after container checks and application image publication succeed.
- Maintainer checklist for release preparation and GitHub repository settings.
- MIT license for the software, with third-party data terms preserved.

### Changed

- Application journey deployments advance the database migration history; rolling back requires a database rollback plan rather than reverting the application image alone.

- Compose deployments pull a published PostgreSQL/PostGIS/pgvector image from GHCR instead of building it on the host.
- Fresh Compose deployments start with an empty database; searches begin after a user adds a location in the app.
- Production career discovery defaults to 12 pages and 48 requests per company, skips robots.txt unless enabled, and shares per-origin pacing with Retry-After cooldowns. Public feed and HTML extraction now retain employer provenance and scoped posting evidence.

### Fixed

- Cancelled company searches avoid a database lock cycle and preserve leases owned by newer discovery attempts.
- Large job searches reuse normalized location aliases to reduce repeated geography processing.

- State-wide map scans query bounded sections and subdivide timed-out sections; incomplete Overpass timeout responses qualify for scheduled retries instead of failing permanently.

- Discovery progress counts overlapping links to the same source posting once and explains why source totals differ from filtered location results.

- Lever discovery and refresh enumerate bounded posting pages while preserving incomplete-scan lifecycle protection.
- Shared ATS utility URLs no longer become false employer tenants.
- Sparse applicant sections gain bounded detail recovery; explicit one-of scripting lists retain verification uncertainty instead of separate mandatory skills.
- Scheduled job refreshes revisit saved feeds and career pages instead of repeating company and homepage discovery; failed fetches retry the saved source with backoff.

- Feed scans import the SQLAlchemy relationship loader used to load existing job locations.
