# Changelog

Notable changes to Radius are recorded here. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The application
version lives in `pyproject.toml`; the private frontend package has its own version.

This changelog starts with the repository maintenance setup. Earlier development
is available in the [commit history](https://github.com/lr101/hiring-scraper/commits/main/);
no historical releases have been reconstructed.

## [Unreleased]

### Changed

- The matching model is switched by hand only: Settings → Matching model chooses **GPT-6 Luna** (default) or **Local models**. The five-hour and weekly usage caps (`RADIUS_MATCH_LUNA_MAX_5H`, `RADIUS_MATCH_LUNA_MAX_WEEKLY`) and the automatic fallback to the local models are removed. With Luna selected, an unreachable ChatGPT pauses matching and keeps work queued instead of using the local models. The `llm` and `llm-reranker` containers now start only with `COMPOSE_PROFILES=local-models`, so they can stay off (about 6 GB less RAM). A stored `luna_first` setting reads as `luna`; `PUT /api/v1/matching-model` takes `luna` or `local`, and `GET` returns `{mode, ready, reason}`.

### Changed

- The job page is redesigned to answer "might this fit?" at a glance. The header holds the role, company, place and work style with two actions (**Start application** and **Original post**) beside a fit summary: score, verdict, how many duties, must-haves and nice-to-haves your CV shows, and what to check before applying. Key facts follow as a column overview, two rows on desktop (location over work style, salary over employment, experience over languages, posted over deadline, …). The role description comes next, then two collapsed sections, **Each requirement against your CV** and **How this score was built**, which the fit summary's links open; the application stage sits beside the posting. Removed: the duplicate "Choose language and instructions first" link (it opened the same page), company website and listing links (on the company page), and first/last seen, posting status and location precision cells. A closed posting shows a closed notice.
- Job descriptions keep the posting's own structure: every source (schema.org pages, Greenhouse, Lever, Ashby, Personio, RSS and career-page detail pages) now stores headings, paragraphs and bullet lists instead of one flattened line, and recruiter tracking tags such as `#LI-JT1` are dropped. The job page renders them as sections and lists with readable type, stays open through the duties and requirements, and folds company story, benefits and legal text behind **Show full description**. Older flattened descriptions get their recognizable section headings and `•` lists back for display. Changed descriptions re-queue posting analysis once on their next crawl (within the usual age and similarity gate).
- Lever postings include their requirement lists and closing text, not only the intro paragraph, and Ashby postings use the HTML description. Requirement headings such as "What you will bring" are recognized.

### Fixed

- Feed and discovery workers merge rows that share a posting id instead of saving them in turn, so a poorer reading of a posting can no longer overwrite a better one. Career pages whose structured data already describes the posting skip the less precise heading-and-paragraph reading, and one failing extraction strategy no longer discards the jobs the others found; such a page is marked incomplete so no postings are closed from it. Arbeitsagentur postings keep the town from the search result when the detail record only names a country, and name the state when no town is given.
- Job pages whose structured data carries its own posting id no longer also save a second copy read from the page HTML (for example Hitachi), and region-only structured addresses read "California, United States" instead of "CA". Schema.org DataFeed postings keep their country codes.
- Career-page job cards keep their work location when it sits beside the title link rather than inside it (for example Paradox career sites such as Trimble's), with "+ N more locations" suffixes trimmed. Detail pages without structured data read explicit `Location:` / `Standort` lines, spelled-out countries ("Brasov, Romania") set a country code, "on site" counts as on-site work, and titles like "AVP …", "Director …" or "Head of …" are no longer skipped. A job page that yields both a structured-data row and a plain-HTML row for the same posting now saves one merged posting; before, the plain row could overwrite the structured location (for example FDM Group vacancies). Screen-reader-only labels such as "All Locations" are no longer read as part of the location, and links like "Internal Vacancies" are no longer saved as jobs.

### Added

- Local LLM job-analysis pre-ranking gate and age cutoff (migration `0047_analysis_prerank`): automatic analysis now skips postings older than `RADIUS_ANALYSIS_MAX_AGE_DAYS` (default `14`, using safe text comparison on ISO `date_posted` with `first_seen_at` fallback) and postings whose `multilingual-e5-small` cosine similarity against all profiles falls below `RADIUS_ANALYSIS_MIN_SIMILARITY` (default `0.80`), processing eligible postings in descending similarity order (preserving local-area preference when a profile has a local radius). Postings saved by the user, moved to a non-automatic Journey stage, or opened on the job page override the gate with priority.
- Local matching progress (`GET /api/v1/analysis-progress?profile_ids=`) lives in **Settings** instead of Journey, counts only gated work toward `queued` and ETA, and adds a muted `skipped` count for postings excluded by age or low similarity.
- Job details show the posting's field, industry, quoted duties, must-haves and nice-to-haves without needing a profile. This replaces the static skill evidence list.
- Local model-based job matching: a local LLM (Qwen3.5-4B, CPU, own `llm` container) lists each posting's duties and requirements as verbatim quotes, and a local reranker (Qwen3-Reranker-0.6B, `llm-reranker` container) checks them against your CV lines in the context of the job's field. Benefits and company text no longer count as requirements, and shared words from unrelated fields no longer produce strong matches. Migration `0045_local_job_analysis` adds the analysis, evidence and queue tables; see `docs/local-models.md`.
- The job page shows how the match score was built: earned against possible points per part (duties, must-haves, nice-to-haves, role, field), deductions, the calibrated score, and a checklist of each duty and requirement marked as shown, partly shown or not shown in your CV.
- Today's focus on Journey: up to three roles to act on now (prepared documents first, then Interested, then best match) with a "3 a day" progress indicator. Ready documents open directly in review.
- Persist PostgreSQL application preparation jobs and checkpoints across worker restarts. Migration `0040_application_preparation` adds the queue. Existing drafts stay on the private runtime volume.
- A private application-generation benchmark runner and provenance-checked comparison of independent fit and checklist scores against frozen baselines.
- Source-checked requirement evidence for application drafting, separate completeness checks, and an independent final content review that blocks unsupported claims.
- Automatically prepare documents for each profile's 25 highest-ranked New applications when subscription usage permits.
- A guided application preparation flow with progress, a faster draft-only mode, instance and per-application model selection, and mobile-friendly revision comparison.
- A fictional bilingual CV and portrait fixture for application testing.
- Resumable application preparation with a backend-saved ChatGPT connection, automatically generated CV and cover letter, prompt-based document revisions, and human confirmation before extension autofill.
- Saved profile photos reused in English and German CV previews, PDF exports and source + photo ZIPs, with per-application exclusion and temporary replacements.
- Local content-based profile matching across skills, summaries, work descriptions and career evidence, allowing unfamiliar job titles to surface in Journey with explained overlap and existing qualification checks.
- Local multilingual semantic profile matching with cached section embeddings, background CPU inference, explained possible leads for unfamiliar wording, and migration `0034_profile_semantics`.
- Optional PNG CV photos, restricted-template PDF rendering, and source + photo ZIP downloads for custom templates.
- Language-specific example cover letters in profile content, used as structure and voice references in application briefs.
- Independent German and English profile content tabs, shared contact and factual preferences, and German application, CV-formatting and LaTeX prompts in Radius and the browser companion.
- English and German LaTeX base CVs with marked work and study history extraction, starter templates, and automatic application-language selection. New CV uploads require `.tex`; PostgreSQL deployments apply migration `0031_cv_languages` during startup.
- Current-job application preparation in the extension: fill details first, open ChatGPT with a copied brief, import results, and download validated LaTeX/PDF documents while Radius syncs in the background.
- Reusable career evidence, writing samples, goals and working preferences for application briefs.
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

- Matching uses GPT-6 Luna through the ChatGPT subscription first and the local models as backup. Luna runs only while the five-hour window is below 60% and the week below 70% (`RADIUS_MATCH_LUNA_MAX_5H`, `RADIUS_MATCH_LUNA_MAX_WEEKLY`); any error or limit falls back to the local LLM and reranker. Settings → Matching model switches between **Luna first** and **Local only**. Luna evidence has its own score calibration, and recorded Luna outputs for the public evaluation set (`tests/eval/public/v3_luna`, `--provider luna`) must pass the same regression gates. `job-analysis-worker` now mounts the `radius_codex` volume.
- Settings is reorganised by importance: a status band with local matching progress and ChatGPT subscription usage (5-hour and weekly) comes first, then Matching, Applications, Job sources and Appearance, each as a section of rows with the setting on the left and its control on the right. A sticky section bar jumps between them. The application model saves on selection and the theme is a segmented control. `GET /api/v1/application-preparation/usage` reports the subscription windows without starting a model turn.
- The public matcher evaluation set is reduced to 71 labelled cases (every grade per profile, every hard-hide category and adversarial tag, complete DE/EN pairs) so recorded model outputs re-record in about 25 minutes; the baseline was recomputed on the same cases with the previous matcher.
- Requirement evidence checks more CV lines: the reranker now reads the 8 closest lines instead of 3, always including at least 3 job, study or saved-duty bullets, so a German requirement can find the matching English CV bullet instead of a skills-list line. Personal traits such as "strukturierte Arbeitsweise" or "communication skills" no longer count as missing must-haves; job pages list them as "Personal qualities (not scored)" to show in the cover letter. Matches are rescored after the update.
- Application preparation shows one CV at a time: the base CV editor is hidden once a tailored CV exists. **Improve with AI** opens its prompt next to the clicked section and shows the suggestion there. Above the editor, the "Mark AI changes" toggle, the findings count, the unreviewed-download notice and the review-complete banner are gone; document-level notes collapse into one line. CV tailoring and revisions keep the base headline and profile format (separator, part count, sentence count, paragraphs, length) and change only the wording.
- Generated CVs show each role in capitals with its location, then the employer in bold italic with dates as `MM/YYYY – MM/YYYY` (`present`/`heute` for current roles). The name is larger, the headline bold and the contact links black.
- Germany and state job searches use the work location only. The employer's or job feed's country or state is no longer a fallback, so a German company's role in Romania is not listed under Germany, A posting that states no work location at all stays in Germany and state searches and in Journey when its employer is in that country or state, flagged "Work location not stated in the posting" (hidden when a profile excludes uncertain matches).
- Journey shows a job only after the local models have analysed it; until then it appears as "Analysing job…". Jobs in your search area are analysed first. After a job or CV edit, the previous match stays visible until it is refreshed. Score contributions are shown with one decimal.
- Matcher v2 is the default (`RADIUS_MATCHER=v2`; set `v1` to return to the previous matcher for this release), with calibrated local job-fit scores from 0–100. Job details show the score contributions and supporting quotes. Language and work authorization contribute a combined deduction of at most five points on visible jobs. Only explicit, cited incompatibilities hide jobs during matching. The versioned background rebuild refreshes Journey rankings. Known limitation: on small held-out evaluation slices some score-range and on-site hide-recall targets are still below goal.

- Publish profile matches and Journey changes with a version check instead of exclusive profile locks. Publishers no longer block each other, and a profile edit in progress defers background publication instead of timing it out. Migration `0041_profile_source_version` is required before starting updated workers.
- Write paths declare job, feed, company, profile and vector changes explicitly instead of relying on database flush listeners. Undeclared changes are logged as errors.
- Rank static matches by documented duties when the posting and profile contain enough work evidence. Show which duties match directly, which experience transfers, and which duties need verification.
- Run Compose migrations once per deployment and restart each worker independently of the API. Deploy the Compose file and its matching app image together.
- Jobs missing from a successful complete source scan close immediately. In Journey, Waiting for reply and Automated application move to Rejected; Accepted and Rejected stay unchanged, and other stages move to Closed. Failed or partial scans preserve existing jobs, and reappearing postings reopen without resetting Journey outcomes.
- ChatGPT application briefs use available personal context directly and request finished documents without confirmation placeholders. Returned applications show downloads first with optional editing; unfinished drafting text is blocked from export. A realistic two-page CV fixture exercises layout and overflow.
- Application journey deployments advance the database migration history; rolling back requires a database rollback plan rather than reverting the application image alone.
- Compose deployments pull a published PostgreSQL/PostGIS/pgvector image from GHCR instead of building it on the host.
- Fresh Compose deployments start with an empty database; searches begin after a user adds a location in the app.
- Production career discovery defaults to 12 pages and 48 requests per company, skips robots.txt unless enabled, and shares per-origin pacing with Retry-After cooldowns. Public feed and HTML extraction now retain employer provenance and scoped posting evidence.

### Removed

- The static keyword matchers (v1 rules and v2 additive scorer), their section vectors (`match_semantic_documents`) and the `RADIUS_MATCHER` setting.
- AI job-fit assessment (ranking queue, status page, score overlay, and standalone ChatGPT fit brief) removed; ranking is local-only (rules matcher + local semantic evidence). Migration `0043_drop_ai_assessment` drops the AI assessment tables and invalidates legacy derived match state for local rebuild while preserving user-set Journey stages.
- **Breaking:** remove SQLite support. Radius, local development and the test suite require PostgreSQL with PostGIS and pgvector; use `compose.dev.yaml` or `scripts/dev/postgres-sandbox.sh`.
- Remove the proof-of-concept crawl runner (`python -m hiring_scraper`) and the research experiments, captures and reports from the repository. They remain available in Git history.

### Fixed

- Precompute per-job Germany and German-state scope membership (`germany_candidate`, `location_states`, `location_scope_version`) in PostgreSQL via bounded worker backfill and invalidation triggers added with concurrent indexes in migration `0046_job_location_scope`, and compute Germany/state snapshot candidate IDs and source counts in one indexed query so `search-snapshot-worker` no longer hits statement timeouts scanning hundreds of `ILIKE` patterns.
- Prevent a deadlock between editing a managed feed and a concurrent crawl of the same feed.
- Match jobs saved from the browser companion like crawled jobs, so they appear in Journey with a score.
- Upgrade populated databases through migration `0033` again and keep a Journey stage saved while duplicates are being consolidated.
- Create the `hiring_app` role in the development migration script, build the browser-test database from the checkout instead of pulling it from GHCR, and refuse to run tests against databases not named `hiring_test*` or `radius_test*`.
- Stop the profile matching worker from scanning every active job for missing vectors every two seconds. Edited and new postings get vectors right away; the rest of the inventory is checked in small windows.
- Stop the semantic search worker from rescanning the whole job inventory on every idle poll, which caused statement timeouts on large databases. Once the backfill for the current model completes, only queued changes are indexed.
- Stop feed, discovery and enrichment writes from locking profile rows, and make profile locks compatible with foreign-key inserts. This removes the `user_profiles` lock timeouts between workers. A changed job is now rematched on its own instead of forcing a full profile rebuild.
- Retry feed and discovery persistence after short database contention instead of discarding the fetched results and crawling the source again. Expected contention is logged as one warning line instead of an error traceback.
- Restore semantic job search on PostgreSQL, which failed with an undefined model revision after the search workload was separated from the API.
- Recover CV and cover-letter formatting without discarding valid documents: correct only rejected edits, retain safe wording when correction fails, normalize simple letter formatting, and provide clearer preparation feedback.
- Keep expired ChatGPT sign-ins in a waiting state until runtime cleanup releases the connection, so an immediate sign-in retry can proceed.
- Separate Journey vacancy identity unions from the short search-generation commit fence, defer busy background pages before row locks, and batch unchanged identity checks. Deploy with a coordinated application/worker restart so old and new identity writers do not overlap.
- Queue production Journey updates transactionally, commit restartable 100-job profile backfill pages, and keep saved Journey pages usable while matching catches up. Migration `0037_journey_match_progress` adds durable queue and progress state.
- Show an accessible score graphic on job details and separate task evidence from desired titles.
- Match documented core duties across job titles while preserving academic scope, formal qualifications, language requirements and source-backed evidence.
- Handle plain-text application save failures and give multi-pass CV preparation a larger review budget.
- Sort Journey by stored match scores while matching rebuilds, keeping rank and displayed cards consistent.
- Allow application startup with native Codex temporary helper symlinks without following their targets during private-volume ownership repair.
- Ground tailored application examples in supplied role evidence, avoid inferred employer duties for title-only postings, and provide original CV macro budgets during drafting and review.
- Keep saved applications available when a match rebuild conflicts with an inventory update.
- Correct malformed generated CVs and cover letters once, and create cover-letter exports from the reviewed text.
- Show the actual error when applying a suggested document revision and isolate draft state in development previews.
- Align the Open original post button to the left of job cards, including when Undo is available.
- Preserve the original CV template, header, history and layout during application tailoring, allowing only marked Kurzprofil and description edits with fixed-line budgets. Cover letters use a separate business-letter template and stacked contact details.
- Accept independently translated English/German base CVs in the companion while retaining career identity checks.
- Keep the reviewed fill action available on linked application steps that contain only job-specific questions.
- Remove URL credentials, queries and fragments from captured form data before saving or prompting.
- Serialize preparation, revision and draft writes with profile and CV deletion so concurrent requests cannot restore deleted application data.
- Restore Python 3.14 in production and CI containers so the locked ONNX Runtime dependency has compatible wheels.
- Invalidate filtered Journey pages after a status change moves a job between checkpoints, preventing stale pagination offsets from skipping jobs.
- Journey loading no longer rebuilds every profile/job match after editing application wording, translations or contact details. Concurrent and repeated reads share requirement extraction while checking the source hash and matcher version. Journey retries once if a background inventory update interrupts its match rebuild.
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

