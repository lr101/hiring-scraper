# Application companion: second implementation slice

First slice and removal of Start application from Journey feed cards: `640eb3be`. Discovery and job-detail actions remain available.

## Implemented

- Explicit **Prepare from these questions** captures the linked application's current labels, required fields, answer limits, native select options and upload formats. It excludes entered applicant values. Radius includes this schema in its preparation brief for both attached and browser-local CVs.
- Optional `form_answers: {form_id, answers: [{id, value}]}` extends the existing package format. A SHA-256 digest binds answers to the current URL, requisition headings/page identity and full visible form shape. Changed questions/options/limits prevent stale tailored answers. Unknown facts and manual/custom/consent fields are omitted. Existing answer-only packages remain supported.
- PDF and DOCX exports for prepared CVs and letters. Paragraph-edit CVs preserve their original DOCX layout; text CVs and letters use a simple layout. Version-bound, bounded rendering performs no application-stage writes and persists no generated files.
- Explicit **Attach prepared documents** selects files only for unique, empty, visible native CV/letter upload fields. It chooses PDF or DOCX according to `accept`, preserves existing selections, pins the browser document, checks current job/profile/CV/package and form before changing fields, and reports rejected or replaced controls. Selecting a file may immediately upload it on a real ATS; the action is always started by the user. No Next/Review/consent/signature/Submit actions are added.

## Public application trials — 18 October 2026

The public probes were strictly read-only: GET/HEAD only, no typing, no file selection, no consent, no submit or navigation button clicks. All attempted non-read requests (including analytics/CAPTCHA initialization) were blocked. This can limit what a page displays, so these results establish form structure, not a completed live application.

| Source | Observation | Resulting reliability improvement / boundary |
| --- | --- | --- |
| [Mail & Media / Greenhouse](https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4399833101) | Wrapper returned 200; real application is in a Greenhouse iframe. Opening that public frame URL directly returned 200 and exposed 21 visible controls. Three upload inputs share the label “Anhängen”. | CV/letter recognition also uses unambiguous native IDs (`resume`, `cover_letter`). The third generic upload remains manual. The embedded URL carries volatile token parameters and cannot be linked as a permanent application. A canonical Greenhouse posting URL also returned 200 without an inspectable top-level form in this probe. This wrapper/iframe path remains manual; frames are not injected automatically. |
| [Engel & Völkers / Lever](https://jobs.eu.lever.co/engelvoelkers/e8c91d6d-2775-4fb5-b58e-1b810d5ebc30/apply) | 200; seven visible controls including a resume upload, contacts and marketing consent. Some labels include loading/status text. | Visible native upload supported; consent remains manual. Custom location widgets remain subject to exact-option verification. Status/conditional changes invalidate captured answers conservatively. |
| [amiconsult / Personio](https://amiconsult.jobs.personio.de/job/1851542/apply?display=en) | 200; seven visible text/select controls. Requiredness appears in label text rather than native `required`. Two document inputs are hidden behind custom controls. | Capture recognizes explicit required-label cues. Hidden uploads are reported for manual review; no clicking custom upload widgets. |

Reproduce: `uv run python -m experiments.application_companion_dry_run`. Sanitized structural output is in `reports/application-companion-form-trials.json`; volatile URL tokens/fragments are removed. No full page or applicant content is saved.

## Isolated application trials

Real Chromium loads the extension. Fictional applicants and intercepted employer forms exercise question capture, exact structured textarea/select answers, consent preservation, changed-form refusal, native document identity, ambiguous/hidden controls, existing uploads, repeated attachment a changed requisition heading at the same URL, and a site rejecting a selected file.

Production-flow trials use the installable ZIP, actual React production build, isolated SQLite API and real LibreOffice PDF rendering. Both attached-CV and browser-local-CV paths capture questions, include them in preparation, accept a return link, retain structured answers through the extension bridge, download actual CV DOCX and letter PDF, fill answers, and select DOCX/PDF files through the real document endpoint. They assert zero submit events and zero tracked application rows.

The rejected-file trial exposed Chrome's loss of asynchronous injected exception messages. The action now returns a bounded error result so the popup reports that the site did not retain the file rather than implying success.

Backend review found newline normalization could conceal a raw TODO paragraph. Raw edits are now checked before normalization, with DOCX/PDF regressions; empty paragraph deletion still works.

Whole-slice review exposed a conditional-form issue: an earlier selection could change a later question while filling retained the earlier answer map. Filling now compares the live canonical form and control identity before subsequent mutations and after asynchronous dropdown waits, stopping on change. Structured packages also disable every legacy answer fallback; safe bound availability answers can fill missing reusable values while known facts and manual-decision restrictions keep priority. Both preparation screens label older answers only with their original question IDs when the captured form hash differs, instead of showing unrelated new question labels.

## Remaining manual work

CAPTCHAs, signatures, consent, authorization decisions, custom/hidden upload widgets, rich editors and embedded forms remain manual. File selection verifies the browser control, not server-side receipt; check the site's upload confirmation. No live filled/uploaded application was attempted, and nothing was officially submitted.

## Verification

- Production frontend TypeScript/Vite build passed; extension formatting and 29 Node tests passed.
- Full Chromium extension suite: 76 tests passed. After the final heading guard and answer-review UI changes, the eight form/attachment trials and both real API/React document flows passed again (10 tests).
- Document rendering, original CV/template exports and profile CV behavior: 26 focused Python tests passed, including real LibreOffice conversion.
- Five frontend scripts passed: preparation storage/returns, handoff/export validation, captured-form validation, application interactions and statuses. CI now runs the added form contract check.
- Final review fixes: three new runtime regressions demonstrated failures and then passed; all 21 selected form, real export/attachment and legacy/preference safety checks have passing final evidence. One aggregate run passed 20 with a test-helper tab-name error; correcting that selector and rerunning the failed production flow passed. No runtime change followed those checks.
- Backend task review: approved after the raw-placeholder fix. Final scoped whole-slice re-review of `9e9a9afc..76c6632d`: approved; both Important findings and the label-review finding addressed, no new issues found.
- CI provisions the same LibreOffice Writer/font packages as the application Docker image so the real PDF tests have their renderer. Workflow parsing passed; remote CI execution was not performed.
