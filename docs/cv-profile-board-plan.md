# CV profile job board implementation plan

The user's CV and instruction are the specification: create a useful profile and regional board, manually assess results with cheaper subagents, improve parsing/filtering/ranking, compare alternatives, and stop within one five-hour session. No user input prompts. Start: 2026-10-06 22:00:59 UTC; initial deadline: 2026-10-07 03:00:59 UTC. A subscription interruption stopped active work at about22:41UTC. The user explicitly resumed at2026-10-07 06:16UTC; continuation is bounded to90minutes (finish by07:46UTC), keeping total active effort belowfivehours.

## Global constraints

- Work in the existing isolated worktree. Preserve existing data and unrelated edits; do not publish or send messages externally.
- Use evidence from the CV. Heidelberg is explicit; a 35 km radius is a provisional search setting. Work authorization, exact years, work styles, salary and current student status are unknown. Do not infer employment from the PDF filename.
- Store an anonymized skills/roles profile, with language levels and academic/professional evidence. Do not commit the CV, contact details or extracted full CV text.
- Public sources only; pace, capture and bound acquisition. Incomplete source reads cannot close jobs. Keep coverage and uncertainty visible; do not claim exhaustive coverage.
- Preserve existing API behavior for profiles without new fields. Matching/filtering happen before pagination. Skill mentions are not automatically mandatory requirements. No live model calls in application runtime.
- Add regression tests before runtime behavior changes; verify the red/green cycle. Existing baseline: 219 tests pass.
- During explicit continuation, independent implementation writers may run concurrently with exclusive file ownership and stable documented contracts; reviews remain scoped and read-only. Use cheaper models for bounded audits and implementation, with no nested subagents. Every implementation task receives a scoped review.
- Judge improvement against independent manual labels; retain a held-out sample. Iterate only for identified errors or measured gains. Report saturation and residual limitations candidly.

## Task 1: Evidence-aware profile matching

Ownership: hiring_scraper/matching.py, a focused supporting matching vocabulary/requirements module if useful, tests/test_profile_matching.py and new matching tests only. Do not edit profile/API/frontend/acquisition files.

Read /tmp/hiring-cv-review/cv-facts.json and cv-audit.md. Expand English/German role and skill aliases for project coordination/management, product development/operations, requirements/business analysis, process improvement, training/L&D, program/executive coordination, innovation and CAD/prototyping. Gendered German role endings and PMO should match. Do not equate all developers with software or product engineering, or training benefits with a job requiring training delivery expertise. Preserve existing software and accounting profiles.

Improve the score so a richly documented CV is not penalized for possessing extra skills. With desired roles, role fit must dominate generic transferable mentions; unrelated developer/sales/nursing roles cannot become recommended just from CAD, customer service, onboarding, Excel or stakeholder mentions. A title-only matching job remains a possible lead rather than a confident recommendation. Match explanations need evidence, recognized missing requirements, academic-skill context, and uncertainty. Keep score/eligible/uncertain/reasons/conflicts/unknowns existing fields; add fit_tier (recommended/possible/unlikely) and any focused explanatory fields needed. A reasonable default board threshold is 30; calibrate with subsequent audits.

Support optional profile language_levels (language -> A1/A2/B1/B2/C1/C2/native) and skill_evidence (skill -> context/note dictionary) without requiring callers to supply them. Exact required CEFR above a known candidate level causes a conflict; vague fluency language stays a review gap unless an explicit level is stated. Optional language/experience wording must not become a hard filter. Extract German/English explicit minimum experience and stated qualification/specialist-domain requirements using sentence/section context; keep unverified qualifications as review gaps, not invented CV capabilities. Do not hard-exclude a candidate on estimated CV years; legacy explicit experience preferences remain supported.

Write focused failing tests for the CV-specific misses and false positives, CEFR comparisons, optional vs mandatory statements, unrelated employer-history/benefits text, and richer-profile ranking. Bump the enrichment rules version when derived signals change. Report red/green evidence, files, test results, assumptions, and commit only owned files.

## Task 2: Regional source adapter and repeatable acquisition

Ownership: new hiring_scraper regional source parser/client module, new app acquisition/import CLI, related source tests, HTTP/worker/ATS integration only where required. Do not edit matching/API/frontend files.

The public Arbeitsagentur Jobsuche search currently returns ergebnisliste (with stellenangebotsTitel, firma, stellenlokationen coordinates, homeofficemoeglich, referenznummer), not the older stellenangebote shape. A captured search and detail example exist in /tmp/hiring-cv-review/ba-probe.json and ba-detail-probe.json. Implement an explicitly bounded, opt-in regional acquisition command with a fresh output directory, search pagination, term manifest, full detail fetching, per-origin pacing, retries/error capture, and a total request budget. Preserve source URLs, reference IDs, timestamps, raw source evidence, geocoordinates and country. Separate home-office-possible (hybrid/unknown) from fully remote. Validate HTTP hosts/paths; no arbitrary URL service. Treat this as an observed public endpoint without a guaranteed official stability contract.

Use broad role families, including English/German project/product/process/training/program/design terms. Save compact reusable data and import idempotently per employer/reference into the app. Refreshes preserve richer descriptions when detail fetch fails and never close jobs based on a selected/incomplete search. Provide a repeatable refresh path for saved area/profile searches; a configurable optional worker/CLI is sufficient. Employer sources can supplement this adapter without invented records. Do not conflate staffing agencies with verified direct employers.

Tests must cover both observed/legacy response shapes if supported, pagination/partial errors, malformed items/coordinates, source reference identity, full detail preservation, remote-vs-hybrid, repeated import, and no lifecycle changes after incomplete reads. Commit owned files and report evidence.

## Task 3: Profile fields, geographic eligibility and deduplication

Ownership: hiring_scraper/app/profiles.py, hiring_scraper/app/api.py, hiring_scraper/app/enrichment.py source inputs only if needed, focused geography helper, and profile/geographic/deduplication integration tests. Do not edit matching, acquisition or frontend.

Persist/validate optional language_levels, skill_evidence, summary, search_area (label/city/latitude/longitude/radius_km/country_code), secondary_roles if useful, education (list of level/field/note dictionaries, level vocational/bachelor/master/doctorate) and certifications (list of explicit credential terms), and matching defaults in profile JSON preferences with backward-compatible empty values. Reject invalid CEFR/coordinates/radius and unexpected structures. Existing save operations must preserve defined new fields. Education evidence will allow later matching refinements to verify a generic degree without inventing a specialized field or higher degree.

Honor regional coordinates; city-text fallback may supply a match only when coordinates are absent and the country is compatible. Explicit remote scope limited to another country must not appear for a Germany profile; unspecified remote scope remains a visible possible lead with a country-eligibility gap. Hybrid office access outside the radius is not global remote. Explicit source expiry in validThrough/valid_through should exclude a past-deadline record without changing its lifecycle based on search absence; expose expiry on job details. Apply geography and profile filters before pagination for SQLite and PostgreSQL consistently.

Deduplicate the same verified vacancy across provider feeds/employer map establishments using canonical vacancy URL (strip tracking parameters while retaining identity parameters and meaningful role fragments) or explicit requisition identity. Prefer the richer current record. Do not collapse different roles or same-title distinct vacancies merely by title. Report counts/coverage, recommended/possible counts and why jobs are filtered in a way useful to the frontend. Keep detail explanations consistent with the board.

Regression tests include US-only remote excluded for Heidelberg/DE, unknown-scope remote flagged, hybrid outside radius excluded, wrong-country/wrong-coordinate city labels, tracking URL duplicates across establishments, and distinct requisitions/fragments preserved. Commit owned files.

## Task 4: CV profile seed and usable board

Ownership: frontend/src/App.tsx, ProfilesPage.tsx, JobEvidence.tsx, DetailPage.tsx, styles.css, anonymized profile fixture/import helper, seed/preview integration, related UI/seed tests. Do not edit matching/acquisition/API contracts except coordination-approved small fixes.

Create the anonymized CV profile with verified skills, professional vs academic/research notes, primary/adjacent roles, Spanish native/English C1/German B2/French A2, unknown exact years, Heidelberg 35 km and Germany remote scope. Keep work styles/employment preferences unconstrained unless explicitly represented as provisional user-editable settings. Provide idempotent explicit import and ensure the finished local/preview board has this saved profile and acquired regional jobs; do not globally seed private CV data into every deployment. A sanitized profile fixture for this user task is acceptable.

Make the saved profile's board link open its region and useful default matching threshold. Show recommended matches and possible leads with uncertainty and source/last-seen information; include sparse relevant titles for recall and keep irrelevant zero-score jobs out of the default view. Allow editable profile area/language levels/evidence and broadening filters without input prompts. Show counts/coverage limits and remote-country gaps. Explain score as heuristic overlap, never hiring probability. Preserve the current design, responsive layout and accessibility.

Use browser/API checks for the completed workflow and existing frontend build. Do not create implementation-mirroring tests for visual-only changes; record meaningful behavior checks. Commit owned files.

## Task 5: Evaluation, refinement and final verification

Ownership: experiments/cv_profile_evaluation.py, sanitized labelled fixtures/reports, README documentation; root coordinates any required focused fix tasks through their implementation owners.

Use cheaper read-only subagents to label a stratified real-job set against the CV before seeing algorithm scores, including every default-board result, negative/near misses, local/remote, sparse descriptions and language/domain gaps. Compare original exact-keyword/Dice baseline, role-aware rules, BM25 or weighted lexical retrieval, and optional semantic approach feasibility. Report precision/recall and ranking metrics with explicit limits of a pooled/incomplete judgement set. Keep a held-out set independent from tuning. Do not call a benchmark exhaustive recall of the entire labour market.

Run successive audits; implement only supported fixes, rerun benchmark after each, and stop when reviewed results show no remaining actionable errors or a further variant fails to improve held-out metrics. Record source coverage, parsing misses, blocked/unimplemented employer adapters and saturation. Preserve repeatable evidence and a concise detailed findings report. Run the full Python regression suite, frontend build, fresh import/idempotence and endpoint/browser checks. Apply final scoped code review and fixes, start a fresh isolated live preview using the repository adapter and verify links. Complete before the hard deadline, with time reserved for verification and reporting.

## Task 6: Confirmed vacancy HTML and source-scope preservation

Added by the measured employer crawl; execute after Task 2 and before Task 3. Ownership: hiring_scraper/html_jobs.py, hiring_scraper/ats.py where metadata preservation is needed, related HTML/ATS tests. No matching/API/frontend edits.

Read /tmp/hiring-cv-review/parser-audit.md and captured examples offline. Typeform's careers HTML incorrectly emits Data & Analytics, Engineering, Executive Leadership and General & Administration as vacancies. Skip headings inside explicitly marked department navigation wrappers; a real Data Analyst sibling must remain extractable. General career taxonomy headings without vacancy evidence should remain unconfirmed, not jobs.

EMBO's official Programme Assistant vacancy has a specific single H1, vacancy-detail route, application CTA and deadline, plus a full description within the role content after H1. Support this evidence-confirmed single vacancy even if its title lacks the global role-word regex. Scope its description to the vacancy content; exclude preceding/following staff biographies and other jobs. Generic Careers/About pages must not become postings. Use minimal representative tests; do not commit raw employer pages. Replay the captured EMBO page and verify the recovered description and Heidelberg location evidence.

Preserve schema.org applicantLocationRequirements and validThrough in parsed metadata and country/coordinate evidence in locations. Preserve structured remote office/country fields without converting hybrid or merely available home office into global remote. Structured expiry is evidence, not an absence-based closure. Tests cover source-field propagation through parsing and malformed nested values. Do not weaken robots or TLS handling; the university/hospital blocked reads are recorded access gaps.

After the parser patch, provide a compact verified employer supplement with source URL, observed time, full role text and actual geography for successfully parsed employer vacancies. Existing public feed jobs can also supplement the board; speculative/open applications are not vacancies. The supplement importer/seed belongs to Task 4.
