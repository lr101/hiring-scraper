# Changelog

Notable changes to Radius are recorded here. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The application
version lives in `pyproject.toml`; the private frontend package has its own version.

This changelog starts with the repository maintenance setup. Earlier development
is available in the [commit history](https://github.com/lr101/hiring-scraper/commits/main/);
no historical releases have been reconstructed.

## [Unreleased]

### Fixed

- Keep saved applications available when a match rebuild conflicts with an inventory update.
- Correct malformed generated CVs and cover letters once, and create cover-letter exports from the reviewed text.
- Show the actual error when applying a suggested document revision and isolate draft state in development previews.

- Align the Open original post button to the left of job cards, including when Undo is available.
- Preserve the original CV template, header, history and layout during application tailoring, allowing only marked Kurzprofil and description edits with fixed-line budgets. Cover letters use a separate business-letter template and stacked contact details.
- Accept independently translated English/German base CVs in the companion while retaining career identity checks.
- Keep the reviewed fill action available on linked application steps that contain only job-specific questions.
- Require a click before application-document generation starts, and remove URL credentials, queries and fragments from captured form data before saving or prompting.
- Serialize preparation, revision and draft writes with profile and CV deletion so concurrent requests cannot restore deleted application data.

- Restore Python 3.14 in production and CI containers so the locked ONNX Runtime dependency has compatible wheels.
- Invalidate filtered Journey pages after a status change moves a job between checkpoints, preventing stale pagination offsets from skipping jobs.

- Journey loading no longer rebuilds every profile/job match after editing application wording, translations or contact details. Concurrent and repeated reads share requirement extraction while checking the source hash and matcher version. Journey retries once if a background inventory update interrupts its match rebuild.

### Added

- A guided application preparation flow with progress, a faster draft-only mode, instance and per-application model selection, and mobile-friendly revision comparison.
- A fictional bilingual CV and portrait fixture for application testing.

- Resumable application preparation with a backend-saved ChatGPT connection, automatically generated CV and cover letter, prompt-based document revisions, and human confirmation before extension autofill.
- Saved profile photos reused in English and German CV previews, PDF exports and source + photo ZIPs, with per-application exclusion and temporary replacements.
- Subscription-backed Luna profile ranking with cached authoritative assessments, functional-experience dimensions, a verified five-hour usage guard at 50%, and migration `0035_profile_llm_scores`.

- Local content-based profile matching across skills, summaries, work descriptions and career evidence, allowing unfamiliar job titles to surface in Journey with explained overlap and existing qualification checks.
- Local multilingual semantic profile matching with cached section embeddings, background CPU inference, explained possible leads for unfamiliar wording, and migration `0034_profile_semantics`.

- Optional PNG CV photos, restricted-template PDF rendering, and source + photo ZIP downloads for custom templates.
- Language-specific example cover letters in profile content, used as structure and voice references in application briefs.

- Independent German and English profile content tabs, shared contact and factual preferences, and German application, fit-assessment, CV-formatting and LaTeX prompts in Radius and the browser companion.

- English and German LaTeX base CVs with marked work and study history extraction, starter templates, and automatic application-language selection. New CV uploads require `.tex`; PostgreSQL deployments apply migration `0031_cv_languages` during startup.
- Current-job application preparation in the extension: fill details first, open ChatGPT with a copied brief, import results, and download validated LaTeX/PDF documents while Radius syncs in the background.

- Reusable career evidence, writing samples, goals and working preferences, with a separate ChatGPT job-fit brief.
- LaTeX CV uploads and Canvas source return links, with editable `.tex` and LuaLaTeX PDF downloads for application documents.
- Agentur für Arbeit scheduled scans in the current and upcoming search lists, with Germany ranked-check cadence, due, overdue, and unscheduled counts.
- Open, Closed, and All posting filters, retained closure dates, and a Closed Journey outcome.

- Form-aware application briefs and question answers bound to the captured step, plus explicit selection of current prepared PDF/DOCX documents in native employer upload fields.

- On-demand posting lookup and server-backed extension search for jobs outside the bounded local catalog, with exact URL matching and bounded metadata caching.

- Pipeline-style branch and merge connections between Journey checkpoints, including mobile scrolling and resizing.

- Parallel Journey stages: In GOD's hand for overqualified roles and Automated application for applications sent using browser-plugin autofill, with persistent filters, undo history and application momentum.

- Focused ChatGPT application pages with shorter instructions, one back link, and secondary actions under Options.

- Company-based application download names and an in-app PDF page preview that works without a native mobile PDF viewer.

- Saved custom application instructions per profile, explicit document-language precedence, and more specific cover-letter writing guidance.

- Saved profile CVs (PDF, DOCX and TXT), a one-button application handoff, and a dedicated ChatGPT return/editor page. DOCX-based CV edits retain the original template and export to DOCX or PDF; PDF/TXT imports use an explicitly labeled simple layout. PostgreSQL adds migration `0028_profile_cv`.
- Single-block ChatGPT application packages, optional return links that fill the correct editor, result-file import, and browser drafts with explicit clear and seven-day expiry.
- Track companies and job feeds directly from the Chrome extension, reusing companies by domain or creating new ones; save individual postings to a chosen profile without changing an existing application stage.

- Add the Chrome Application Companion with local CV preparation, job-linked autofill, and tested installable ZIP/checksum builds in CI, Settings, and tagged releases.

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

- Jobs missing from a successful complete source scan close immediately. In Journey, Waiting for reply and Automated application move to Rejected; Accepted and Rejected stay unchanged, and other stages move to Closed. Failed or partial scans preserve existing jobs, and reappearing postings reopen without resetting Journey outcomes.

- ChatGPT application briefs use available personal context directly and request finished documents without confirmation placeholders. Returned applications show downloads first with optional editing; unfinished drafting text is blocked from export. A realistic two-page CV fixture exercises layout and overflow.

- Application journey deployments advance the database migration history; rolling back requires a database rollback plan rather than reverting the application image alone.

- Compose deployments pull a published PostgreSQL/PostGIS/pgvector image from GHCR instead of building it on the host.
- Fresh Compose deployments start with an empty database; searches begin after a user adds a location in the app.
- Production career discovery defaults to 12 pages and 48 requests per company, skips robots.txt unless enabled, and shares per-origin pacing with Retry-After cooldowns. Public feed and HTML extraction now retain employer provenance and scoped posting evidence.

### Fixed

- Captured application questions remain available when the extension delivers them while the saved-CV preparation page mounts.

- Unchanged DOCX application packages remain prepared in the companion, and linked question-only wizard steps can reuse their exact application answers without requiring contact fields again.

- ChatGPT application handoffs open the destination even when clipboard access fails, with a browser copy fallback and selected text for manual copying.

- Opening an original job post leaves its stage unchanged until interest is explicitly selected. Interested roles can move to Not interested through their actions or a left swipe.

- Cancelled company searches avoid a database lock cycle and preserve leases owned by newer discovery attempts.
- Large job searches reuse normalized location aliases to reduce repeated geography processing.

- State-wide map scans query bounded sections and subdivide timed-out sections; incomplete Overpass timeout responses qualify for scheduled retries instead of failing permanently.

- Discovery progress counts overlapping links to the same source posting once and explains why source totals differ from filtered location results.

- Lever discovery and refresh enumerate bounded posting pages while preserving incomplete-scan lifecycle protection.
- Shared ATS utility URLs no longer become false employer tenants.
- Sparse applicant sections gain bounded detail recovery; explicit one-of scripting lists retain verification uncertainty instead of separate mandatory skills.
- Scheduled job refreshes revisit saved feeds and career pages instead of repeating company and homepage discovery; failed fetches retry the saved source with backoff.

- Feed scans import the SQLAlchemy relationship loader used to load existing job locations.
