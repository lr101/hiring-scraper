# Application flow and extension automation review

Reviewed 2026-10-17 against this checkout. This is a source review with targeted executable checks. No production behavior was changed, no employer application was submitted, and no applicant data was used.

The greatest opportunity is to connect the existing preparation and filling components into one continuing application session. Radius already discovers jobs, prepares document briefs, validates returned packages, associates ATS pages with a job, and fills known fields. The remaining work is concentrated at the handoffs: discovering actual application questions, moving generated files into the form, continuing through later steps, and recording the result.

## Current flow

1. Discover a job or capture a company/posting through the extension. Scheduled server discovery and scans already exist.
2. Select a Radius profile and attach a CV. Separately maintain reusable contact details, employment history and common answers in the extension.
3. Open the preparation page, copy its brief, paste/send in ChatGPT, and return through a link or package import. DOCX paragraph edits preserve the original template.
4. Radius saves the draft locally; the connected extension captures it automatically and checks job/profile/CV bindings.
5. Open the employer form. The extension follows explicit Apply navigation, fills known details on request, and reports unresolved controls.
6. Manually upload documents, complete unsupported controls and later wizard steps, review and submit. Update application tracking separately.

Existing capabilities worth extending rather than rebuilding: English/German field aliases, some custom comboboxes, explicit salary/availability/authorization answers, inline work-history row creation, exact job bindings, freshness checks and preservation of existing field values. The application stage API already supports `automated_application`; the shipped companion does not write that stage.

## Fix these blockers first

### 1. Valid unchanged DOCX packages are treated as unprepared

**Reproduced.** The preparation contract explicitly allows `cv: []` when no paragraph edits are needed. The frontend parser and extension importer accept it, but `readyPackage` requires a nonempty edit array. The badge stays grey, and `fillProfile` falls back to reusable details without the package's cover letter or job-specific answers.

Evidence: [frontend package validation](../frontend/src/applicationHandoff.ts#L22), [prompt allowing no edits](../frontend/src/applicationHandoff.ts#L196), [extension import](../extensions/application-companion/sources.js#L68), [readiness check](../extensions/application-companion/vault.js#L182), [fallback when unprepared](../extensions/application-companion/application-state.js#L350).

Accept a validated empty edit list when the bound original template remains usable. Regression gate: import an unchanged DOCX package, show a prepared badge, fill its role-specific answers, and invalidate it after a CV revision.

### 2. Later wizard pages can be rejected despite having matching answers

**Source finding.** Form discovery normally requires at least two recognized reusable fields, or one recognized field plus an upload. `supports()` also requires a recognized field key. A later page containing only employer-specific questions can therefore be rejected even if the saved package contains exact answers. Adding more generated answers alone will not resolve this.

Evidence: [form-root discovery](../extensions/application-companion/best-effort.js#L206), [support predicate](../extensions/application-companion/best-effort.js#L974), [inspection rejection](../extensions/application-companion/content.js#L76).

Allow a previously bound application session to inspect an unambiguous question-only form. Keep authentication detection, document/form identity checks and exact answer scoping. Gate: a two-page fixture with contacts first and only custom questions second.

### 3. Catalog sync favors the first 2,000 records, not current relevant jobs

**Source finding.** The server returns every job in ascending ID order, including inactive records; the extension stops after 20 pages/2,000 jobs. Later jobs can remain absent on every full sync. Popup search searches only this cache. Direct preparation/import by job ID can recover a known job, but browsing a missing job cannot resolve it automatically.

Evidence: [catalog query](../hiring_scraper/app/companion.py#L79), [bounded synchronization](../extensions/application-companion/radius-client.js#L163), [cache-only search](../extensions/application-companion/application-state.js#L220).

Add bounded server lookup by canonical posting URL/provider ID and server search for cache misses; use incremental synchronization and prioritize active/tracked jobs. Preserve exact matching rather than expanding domain-based inference. Gate: recognize a current posting after more than 2,000 older records, including an Apply alias.

## Ranked automation opportunities

Effort is relative engineering scope, not a delivery-time estimate. Priority reflects visible workflow friction; there are no measured user time savings yet.

### 1. Prepare answers from the actual application form — high value, medium effort

The brief contains the posting, CV and supporting profile, but no inventory of employer questions, choices, required fields or length limits. Generated answers must then match the field label after normalization. This makes answer generation and answer placement unnecessarily disconnected.

Add **Scan application questions** from the explicitly linked form. Capture question IDs, labels, input types, choices, length limits, required status and form identity, excluding existing applicant values and authentication fields. Include that schema in preparation and return structured answers keyed to its question IDs, with source references and an explicit unanswered state. For later pages, generate only answers to newly discovered questions rather than regenerate the entire CV.

Reuse [field inventory](../extensions/application-companion/best-effort.js#L240), [answer parsing](../extensions/application-companion/best-effort.js#L278), [exact answer lookup](../extensions/application-companion/best-effort.js#L453) and [prompt payload](../frontend/src/applicationHandoff.ts#L223). Introduce a versioned optional answer contract so existing packages continue to work.

Gate: employer-specific questions in English/German receive the correct bounded answers; changed options invalidate mappings; unknown facts remain unanswered; work authorization and consent cannot be inferred from generic prose.

### 2. Attach the prepared CV and letter directly — high value, medium effort

The preparation page can export the edited DOCX/PDF, but the extension explicitly leaves every file field manual. Cover letters and text-only CVs use a browser print/copy flow. Users must download, find and choose files repeatedly.

Create a versioned document bundle for the exact job/profile/CV/package revision. Expose document retrieval to the connected extension, then offer **Attach prepared documents** for uniquely identified native upload fields. Implement and validate upload behavior per supported adapter; retain a file-picker fallback where the site rejects it. Add direct PDF/DOCX export for cover letters so they do not depend on a print dialog.

Evidence: [CV export request](../frontend/src/ApplicationPreparationPage.tsx#L291), [print-based other documents](../frontend/src/ApplicationPreparationPage.tsx#L311), [file fields always manual](../extensions/application-companion/best-effort.js#L531). The current extension bridge carries package text, not a rendered document bundle.

Gate: correct file contents, filename, type and size; upload widget acknowledges receipt; repeated action does not duplicate attachments; changed documents invalidate the bundle; ambiguous upload fields remain manual.

### 3. Continue an assisted application session across wizard steps — high value, medium–large effort

The extension stores navigation associations and recent-application context, but users reopen the popup and fill again on each step. Form filling runs against a single inventory; newly revealed conditional controls require another run. Generic shared URLs intentionally consume confirmation after a fill.

Add a persistent side panel with the selected job, document version, current step and unresolved required fields. A session-scoped **Fill this application as I continue** option can re-inspect new steps and fill known values after the user navigates. First keep Next/Review under user control; subsequently allow verified adapter-specific Next actions only when required fields and upload acceptance are confirmed. Preserve document-level checks on generic shared URLs.

Evidence: [single fill action](../extensions/application-companion/background.js#L141), [single inventory](../extensions/application-companion/best-effort.js#L475), [page confirmation](../extensions/application-companion/application-state.js#L303), [navigation association](../extensions/application-companion/application-state.js#L602).

Define adapter operations such as inspect, fill, verify, identify-next-step and detect-outcome. Use existing source inventory to choose the first ATS providers, then measure compatibility. Consent, signatures, CAPTCHA, login and final Submit remain explicit user actions in the initial version.

Account for extension access during cross-origin navigation: a session association does not itself grant permission to inject into the next site. Request the next user gesture or narrowly scoped optional origin access when needed, rather than assuming the original temporary tab grant follows every redirect.

Gate: delayed conditional controls, React replacements, back navigation, changed jobs, session expiry, required-field errors and partial uploads; no duplicate mutations or progress after an uncertain result.

### 4. Bootstrap reusable facts once and bind them to each Radius profile — high value at setup, medium effort

Radius stores a matching profile and CV, while the extension stores a separate global `applicant`. Changing the selected Radius profile does not select a separate reusable applicant record. Structured bullet preparation is also a separate copy/paste action rather than part of the standard brief. The extension schema supports employment history but has no structured education/certification timeline for filling those sections.

Extract proposed facts from the saved CV once, let the user correct them, and keep the confirmed record keyed by Radius origin/profile ID. Preserve exact date precision and source paragraph references. Reuse the same stable experience IDs in the main preparation brief, eliminating the separate bullet prompt. Extend structured facts and adapters to education/certifications as needed. Applicant facts can remain local; a shared data contract does not require uploading them to Radius.

Evidence: [Radius profile structure](../frontend/src/profile.ts#L11), [extension profile schema](../extensions/application-companion/profile.js#L25), [global applicant storage](../extensions/application-companion/options.js#L239), [profile selection](../extensions/application-companion/application-state.js#L108), [separate bullet prompt](../extensions/application-companion/options.js#L217).

Gate: switching profiles never reuses another person's facts; incomplete dates stay incomplete; extracted facts remain distinguishable from role-specific rewrites; editing a fact invalidates affected prepared packages.

### 5. Record confirmed submissions and create follow-up tasks — medium–high value, medium effort

The current companion never calls the application-stage endpoint. Filling therefore leaves no durable submission evidence or follow-up trigger. The stage model has creation/update timestamps, while momentum events record applied/interview/accepted counts; neither is a full application-attempt history.

After the user's final Submit action, detect an adapter-specific success outcome tied to this application session. Record a durable submission event containing the attempt ID, job/profile, document revision, timestamp and minimal confirmation reference. Use it to update `automated_application` once and schedule an in-app follow-up reminder. Offer **I submitted this application** when the outcome cannot be verified. Avoid storing entire confirmation pages or regressing a later interview/accepted stage on a delayed retry.

Evidence: [extension message handlers](../extensions/application-companion/background.js#L54), [existing stage API](../hiring_scraper/app/api.py#L2502), [application record](../hiring_scraper/app/models.py#L320), [momentum events](../hiring_scraper/app/models.py#L342). [Application-stage documentation](../docs/application-stages.md) describes a callback contract, but it is not wired into the shipped companion and still refers to an absent proof-of-concept plugin.

Gate: validation failure, network timeout and an unverified thank-you URL never count as submitted; refresh/retries produce one event; a late receipt cannot move Interview back to a sent stage; reminders key off actual submission time.

### 6. Capture a complete posting from the current browser page — medium value, small–medium effort

The popup currently prefills URL and tab title, while company details may still need typing. New one-off jobs are stored from title/URL and source metadata, without the description in that creation path. The browser already has the rendered posting available, including pages the public crawler may not extract fully.

Parse `JobPosting` JSON-LD, canonical/provider identifiers, employer, location, deadline and the visible job description into a preview before saving. Reuse existing company-domain resolution and feed detection. Keep one-off save distinct from tracking an entire company feed. Compare page identity before associating an existing posting.

Evidence: [popup capture defaults](../extensions/application-companion/popup.js#L327), [save request schema](../hiring_scraper/app/companion.py#L32), [new job insertion](../hiring_scraper/app/companion.py#L162).

Gate: multiple-job listings require a choice; navigation titles and tracking parameters do not become job identity; imported descriptions are treated as untrusted source material.

### 7. Replace the manual generation handoff optionally — high friction reduction, larger integration scope

Copying a brief, sending it in another app and returning a generated link remain manual. Keep the existing route, but introduce an optional generation interface that accepts the same bound inputs and writes a validated package directly to Radius. It needs an authenticated result endpoint, asynchronous job state, cancellation/retry, schema validation and visible usage handling. Choose the provider/account integration separately; this review did not evaluate current provider eligibility or APIs.

Evidence: [clipboard and external-tab handoff](../frontend/src/ApplicationPreparationPage.tsx#L209), [return parsing](../frontend/src/applicationHandoff.ts#L22), [browser-local draft persistence](../frontend/src/ApplicationPreparationPage.tsx#L158).

Gate: stale inputs, partial responses, duplicate completion and failed generation preserve the existing package; generated output passes the same validation and document rendering path. Implement after form-aware inputs so automation does not merely produce generic answers faster.

### 8. Build a daily action queue and reusable answer suggestions — medium value, medium effort

Combine interested jobs lacking a package, interrupted sessions, approaching deadlines and submitted applications due for follow-up into one queue. Offer to save a manually supplied answer with an explicit scope: personal fact, employer-specific answer or job-specific text. Do not learn solely from whatever appears in a filled page, since it may contain defaults or generated content.

Use existing profile-specific application stages and matches for queue selection. Add durable preparation/session/reminder records; current browser-local drafts and ephemeral fill reports do not support this reliably. Keep preparation state separate from submitted stages and do not treat opening a job as an instruction to generate or apply.

Gate: no cross-profile answer reuse, duplicate reminders or repeated preparation for unchanged inputs; closed jobs leave actionable preparation queues while tracked applications remain available.

## Suggested delivery sequence

1. Fix the unchanged-DOCX readiness bug, question-only wizard recognition and bounded catalog lookup. Add session-level outcome measurements.
2. Ship form-aware preparation, a persistent application panel and direct document attachments. These remove repeat work from each application.
3. Add adapter-specific continuation, verified submission receipts and follow-up reminders.
4. Improve first-run profile extraction and posting capture; evaluate integrated generation and a daily queue against measured friction.

Measure manual corrections, unresolved required fields, unsupported-control reasons, repeated fills, time from preparation to reviewed form, upload acceptance and verified submission outcomes. Segment by ATS and form type. Field fill count alone can look good while an application remains blocked. Collect structural reason codes and timings rather than applicant answers by default.

## Verification and limits

- Ran `npm --prefix extensions/application-companion test`: **25 passed, 0 failed**.
- Ran `node tests/frontend_application_handoff.mjs`, `node tests/frontend_application_preparation.mjs`, `node tests/frontend_applications.mjs` and `node tests/frontend_application_interactions.mjs`: **all four passed**.
- Ran a separate Node assertion probe: the same `cv: []` package is accepted by frontend/import validation and rejected by readiness; adding one valid paragraph edit makes it ready.
- Inspected the existing browser tests for prepared-package capture, Apply redirects, repeated-step bindings and production preparation. These use intercepted synthetic employer pages and do not establish live ATS compatibility.
- Live browser review was unavailable: `preview_open` returned `Preview automation open failed: This host blocks the sandbox T3's browser runs in (AppArmor on Ubuntu 23.10+). Run sudo env "PATH=$PATH" t3 browser setup on the host once to allow it, then try again.` Python Playwright, SQLAlchemy and FastAPI are not installed in this worktree, so the Python/backend/browser suites were not run.
- No measured completion-time savings, production funnel data, live generation, actual file uploads or employer submissions were available. Rankings are engineering judgments from the traced flow, not benchmark results. Earlier application streamlining reports describe older snapshots; they were not treated as the current feature inventory.
