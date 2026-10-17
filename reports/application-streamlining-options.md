# Application streamlining: review and feasibility trials

Reviewed 2026-10-16 against this checkout. This is an investigation with reproducible experiments, not a shipped integration. Production application code is unchanged. No ChatGPT account was connected, no model inference was purchased or run, and no employer received an application or applicant data.

## Recommended direction

Build a verified career profile, then a **Prepare application** action that produces a tailored CV, optional cover letter and reusable answers with a review step. Add assisted autofill through a browser extension after the application pack is reliable. Keep broad job discovery fast and provide a distinct personalized shortlist.

ChatGPT Pro can help power this, but using a subscription and accessing personal knowledge are separate capabilities. The most useful first release need not depend on OAuth: a reviewed career brief and a job-specific handoff to ChatGPT can establish the workflow first.

## What the app actually does today

- `hiring_scraper/app/profiles.py` stores skills, professional/academic evidence, language proficiency, qualifications, role interests and search preferences. Pasted CV text only yields skill suggestions and is discarded. It does not import a complete employment timeline or generate documents.
- `frontend/src/ProfilesPage.tsx` provides editable evidence, education and certifications. There is no CV/PDF upload, document library, contact/address profile, reusable application-answer bank or provider connection UI.
- `frontend/src/ApplicationsPage.tsx`, `frontend/src/applications.ts` and the application API track interest, applications and outcomes. Opening an original posting does not prove an application was submitted. The `waiting_for_reply` stage is user-declared, not an employer delivery receipt.
- Ordinary `/api/v1/jobs` search deliberately uses a profile for saved geography and tracking, **not profile matching**. Its semantic route ranks query similarity without profile match data. Selecting a profile in broad discovery is therefore not equivalent to asking for personalized recommendations. The application journey separately builds profile matches and ranks by match score.
- `hiring_scraper/app/semantic_search_worker.py` already contains a local multilingual E5 embedding service and an asynchronous indexer. Adding embeddings alone would duplicate existing capability; the opportunity is to combine retrieval with evidence and preferences.
- `ensure_profile_match_indexes` can rebuild all active-job matches synchronously on an application request. Personalized onboarding and richer profiles should use background rebuilds with progress, rather than add more inference inside this request.

These observations come from current code. Historical figures in [the earlier evaluation](../docs/cv-profile-board-findings.md) are useful context but are not a fresh measurement of this checkout's recommendation quality.

## Connecting ChatGPT Pro: feasible options

### 1. Sign in with ChatGPT for model usage

**Possible for eligible deployments.** Current official documentation describes optional ChatGPT plan usage for open-source and locally hosted apps. Paid or remotely hosted products should use the documented interest/access process; this review does not establish that a particular hosted deployment qualifies. Identity sign-in alone does not authorize plan usage. Crucially, this route does not expose conversations or other account context. [Official overview](https://developers.openai.com/siwc/token-sharing-open-source)

For a personal installation, prototype the official OAuth flow with PKCE, account/workspace selection, persistent host identity, token refresh and disconnect. Store credentials in a protected local/backend store, separate from career-profile JSON and browser storage. Discover models for the connected account and verify an actual completed request before displaying the connection as usable. [Official models and inference guide](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)

This route currently requires streamed Responses requests with storage disabled and context provided by the app. Its preview does not support hosted file search, native computer use or hosted MCP tools. Consequently, CV retrieval, rendering and browser autofill must be separate app components; Pro sign-in does not supply them. [Official preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

**Not tested:** account eligibility, consent, quota, token refresh, live inference, generation quality or operating cost. No account credentials were available or requested for this review.

### 2. Work inside ChatGPT with a plugin/MCP connection

Expose narrowly scoped tools such as `get_job`, `get_approved_career_profile` and `save_application_draft`. ChatGPT can retrieve job evidence and return a draft while the user works in their ChatGPT context. Official documentation supports connecting a custom MCP server as a personal plugin. [Official plugin quickstart](https://developers.openai.com/plugins/quickstart)

This is a promising way to use context already available in the active ChatGPT conversation. It is **not** an API to export all account memory, and context availability should be tested rather than promised. Require the user to review career facts before saving them back. A hosted MCP server needs user authentication and profile ownership; the existing single-workspace profile IDs are not an authorization boundary.

### 3. Explicit career-context handoff — best first experiment

Ask ChatGPT to create a structured career brief from information available in the user's current conversation, then let the user review/import that brief into this app. Do not import an entire account archive. Include employment dates, verified achievements, skills with evidence, language levels and preferences; keep unknown facts explicit. Upload the original CV or provide its text separately when fidelity to its contents is needed.

This is the lowest integration effort and makes personal knowledge inspectable. A future **Prepare in ChatGPT** action can export the job description, approved profile facts and a stable draft identifier; the user brings the result back for review. It still involves a manual handoff until a plugin or provider integration exists.

### 4. Standard API provider

A conventional provider API is another implementation route for a hosted service, with its own provider credentials, usage accounting and explicitly supplied career context. Do not assume a ChatGPT subscription grants ordinary API billing credit or imports ChatGPT memory. Keep this behind the same generation interface as any eligible ChatGPT-plan connection so document workflows do not depend on one authentication path.

## CV tailoring that would be useful

Create a reusable career record containing dated employment, projects, education, certifications and achievement bullets. Every fact needs a source reference and user approval. Keep role-specific rewrites separate from this master record.

For a selected posting:

1. Snapshot the job description, source URL, retrieval time and requirements. Refresh a stale or closed posting before preparing the pack.
2. Retrieve relevant verified achievements and separate professional experience from university/research projects.
3. Draft a role-specific summary and reordered/rephrased bullets, with references to the original facts. Return qualification gaps and unanswered application questions alongside the draft.
4. Show a before/after review. Never upgrade German B2 to C1, academic CAD to industrial employment, or an unknown duration to a claimed number of years. Treat instructions embedded in job text as untrusted source content.
5. Render a simple ATS-friendly DOCX/PDF from the approved structure. Store the exact approved version against that application, including profile/job revisions and model/prompt provenance.

The existing sanitized fixture supports selection of relevant skill-evidence excerpts, but does not contain enough identity, employment history or original document structure to reconstruct a complete CV. The trial writes an example evidence packet; it is **not an AI-tailored CV**. Start with English/German variants and inspect text extraction, dates and page layout before releasing document export.

## Autofill: recommended architecture and limits

**Prefer a user-triggered browser extension**, using temporary current-tab access and a side panel with proposed values. Content scripts can inspect and update page DOM; Chrome documents `activeTab` plus scripting as a way to grant access after a user gesture. [Chrome activeTab documentation](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)

The app's own web page cannot directly edit another site's form through an iframe or another tab. The trial reproduced a cross-origin `SecurityError`; this is a browser boundary, not an autofill implementation bug. An extension or a locally controlled browser is needed. A clipboard-only answer panel remains a useful fallback and works on mobile without an extension.

Use a field-confidence layer: map name/contact/links first, preserve existing edits, and preview job-specific answers. Work authorization, sponsorship, salary, notice period and consent need explicit saved answers or applicant input. Provide the approved CV for a user-selected upload. Stop at unsupported widgets, login, CAPTCHA or ambiguous questions; leave final submission to the applicant in the first release. Values entered into real forms can be transmitted before submission through autosave or page scripts, so preview before filling.

**Avoid a universal direct-submit API design.** Public read access does not imply permission or credentials to submit. Greenhouse's job-board application POST requires its Job Board API key. Lever's POST requires an employer account API key, and its public postings API does not expose custom application questions. These APIs are useful for discovery and permitted partner integrations, not a generic job-seeker credential. [Greenhouse documentation](https://docs.greenhouse.io/job-board.html), [Lever's official API repository](https://github.com/lever/postings-api)

Start adapter evaluation with standard Greenhouse/Lever forms, then sample Personio and other providers actually present in the app's sources. Treat multi-step custom/Workday flows as separate adapter work. This review has not established current live-site compatibility for any provider. A general browser agent could be an optional fallback later, after measuring its error rate and cost; it is not the first dependency for basic autofill.

## What was tested

Reproduction, from an environment with this repository's dependencies and Playwright Chromium installed:

```bash
python -m experiments.application_streamlining_trial --browser
python -m unittest discover -s tests -p test_profile_matching.py -q
python -m unittest discover -s tests -p test_profile_integration.py -q
python -m unittest discover -s tests -p test_job_applications.py -q
node tests/frontend_applications.mjs
node tests/frontend_application_interactions.mjs
```

An existing local Python environment was reused without modifying it. Its browser was supplied through the optional `--chromium` executable argument. An initial older environment lacked `pgvector`; the integration tests below were rerun successfully with the complete environment. T3 collaborative preview was unavailable because the host blocked its browser sandbox, so browser diagnostics used the repository's Playwright tooling with locally intercepted pages.

| Probe | Observed result | What it establishes |
| --- | --- | --- |
| 11 synthetic job descriptions × 2 profile variants | Assertions passed using `rules-v20` | Controlled evidence/preference behavior, not real-world precision |
| Two-year minimum, unknown vs hypothetical confirmed two years | 45 / possible → 97 / recommended | A missing factual answer can materially change ranking |
| Five-year minimum under hypothetical two-year profile | Ineligible, despite raw score 82 | Eligibility must take precedence over score presentation |
| Onsite role, no work preference vs hypothetical remote/hybrid preference | Eligible → ineligible | Explicit preferences prevent avoidable recommendations |
| German C1 mandatory vs optional, with fixture B2 | Mandatory excluded; optional retained | Preserve mandatory/optional qualification distinctions |
| Three-year professional SolidWorks requirement vs academic evidence | Explicit verification gaps | Academic evidence is insufficient for this quantified requirement |
| Unquantified industrial SolidWorks experience requirement | Possible, 49; no explicit requirement gap | A remaining evidence-parser weakness to address |
| Master's requirement / sparse description | Review gap / possible tier | Unknown qualifications and missing text need visible handling |
| Three synthetic forms in Chromium | 4 English fields + 5 German fields + 1 review-case field filled | Basic labeled-field mapping and DOM events work |
| Review-case form | Existing edit preserved; 8 unsupported/sensitive fields left for review | Scope can be constrained without guessing answers |
| Cross-origin iframe | `SecurityError` from parent page | A normal app page cannot directly fill an employer page |
| Existing targeted checks | 47 Python tests passed; both Node scripts passed | Current matching/profile/tracking behavior passes these checks |

Browser requests were intercepted locally: **zero employer requests and zero submissions**. The browser trial did not package/install an extension or exercise React-controlled fields, custom dropdowns, file upload, multi-page flows, CAPTCHA, login, real ATS pages, or mobile. There is no measured completion-time saving or recommendation-quality uplift yet. Full inputs' hashes and outputs are in [the JSON artifact](application-streamlining-trial.json), with runnable code in [the experiment](../experiments/application_streamlining_trial.py).

## Better recommendations: priority changes

1. **Ask high-value missing questions.** Relevant employment dates/years, work authorization and sponsorship, target seniority, salary, notice period, commute and work style. Ask once and reuse answers. The fixture has several explicit unknowns. A single overall experience number cannot establish years in each skill/domain; record evidence-specific tenure before using it for specialist eligibility.
2. **Separate broad discovery from a personalized shortlist.** Reuse local multilingual retrieval for bilingual role vocabulary, then rerank a bounded candidate pool using verified evidence, explicit constraints and requirement gaps. Keep a visible exploration lane for adjacent roles. Do not silently convert ordinary browse into a slow profile rebuild.
3. **Improve evidence quality before increasing score complexity.** Hydrate incomplete descriptions, show source/freshness/closing dates and coverage gaps, fix unquantified professional-tenure handling, distinguish optional requirements, and retain remote-country eligibility and duplicate controls. Show “fit evidence / gaps / unknowns” rather than a percentage that resembles a hiring probability.
4. **Learn why users skip.** Record optional reasons such as wrong role, too senior, salary, location or qualification mismatch. Current application status alone does not provide these labels. Do not treat lack of reply or an employer rejection as proof of skill mismatch. Personalize suggestions only after sufficient explicit feedback; keep settings inspectable and reversible.
5. **Prioritize a short actionable queue.** A daily/weekly shortlist, deadline reminders, saved searches, “new since last visit,” follow-up reminders, duplicate-application warnings and a reusable answer bank reduce work beyond CV generation. Existing application momentum events can support progress reporting; draft/submission artifacts and reminders need additional storage.

## Delivery sequence and acceptance gates

| Order | Deliverable | Relative effort | Gate before expanding |
| --- | --- | --- | --- |
| 1 | Reviewed career brief, missing-fact questions, reusable answers and ChatGPT handoff | Small–medium | All imported facts reviewable; unknowns retained; no original-CV storage implied |
| 2 | Prepare application with tailored CV/answers, diff and versioned export | Medium | Blind factual review finds no invented facts; edited source facts invalidate affected drafts; DOCX/PDF inspected |
| 3 | Personalized shortlist using existing retrieval + evidence reranking; background profile indexing | Medium–large | Human-labeled held-out roles improve top-10 relevance without losing eligible leads; no new hard-constraint violations; measured latency |
| 4 | Extension autofill for selected ATS adapters | Large | Representative live dry runs with authorized test data measure field accuracy, correction count and unsupported flows; no automatic submission |
| Parallel spike | Eligible local ChatGPT sign-in or ChatGPT plugin | Medium, deployment-dependent | Actual consent + completed inference, disconnect/revocation and quota handling demonstrated; no promise of memory export |

Effort is a relative engineering judgment, not a delivery estimate. Measure median time from opening a role to an approved application pack, corrections per pack/form, unsupported-field rate, unsupported-claim count, shortlist precision@10 and eligible-lead recall on a held-out labeled pool. Track interview outcomes later with appropriate caution about small samples and selection bias. Establish real baselines before promising time savings or better hiring outcomes.

## Suggested career-context handoff prompt

> Help me prepare a career brief for a job-application tool. Use only career facts available in this conversation or documents I provide. Separate employment from academic/research projects. List employment dates, responsibilities, achievements and their sources; skills with evidence and experience context; education, certifications and language levels; and job preferences I explicitly stated. Mark unknown dates, work authorization, salary and notice period as unknown. Do not invent metrics, employers, credentials or proficiency. Show the brief for my review before I transfer it. For the attached job description, propose CV edits tied to those reviewed facts and list unanswered application questions separately.

This prompt is a proposed manual workflow, not a tested ChatGPT-memory retrieval mechanism.
