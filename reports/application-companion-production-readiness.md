# Application companion production preparation — 18 October 2026

This release adds captured-form preparation, question-bound answers, guarded PDF/DOCX attachment and bounded posting lookup. Journey cards no longer show Start application; the job-detail action remains available. The API, frontend and unpacked extension must come from the same tested commit. No database migration is added by this branch. Deployment and rollback steps are in `extensions/application-companion/README.md`.

## Release review fixes

- Delayed conditional forms: changing a native select, boolean checkbox/radio or custom dropdown stops later mutations when a captured question package is in use. The document retains this pause across repeated Fill actions until an explicit fresh question capture. Automatic document inspection does not clear it. Existing form-shape/control-identity checks still reject changed questions. Chromium regressions first demonstrated stale later answers after all three native choice types and after a second Fill, then passed with the guards.
- Finished exports: text CVs and letters render the brief's simple Markdown headings, bullets and bold spans into escaped DOCX content before optional PDF conversion. Original DOCX paragraph-edit exports continue to preserve the source template. The regression first failed on literal Markdown markers, then verified document structure and actual PDF text.
- Container checks now run the captured-form contract script as well as the extension job. Extension CI installs the same LibreOffice Writer/font runtime used in the application image.
- Merged current `origin/main` (`628135e9`); its only additional change is the Dependabot automation workflow.

Independent re-review approved both runtime fixes after the repeated-fill bypass was addressed; no blocking findings remained.

## Local verification

- TypeScript/Vite production build, extension Prettier check and all 29 extension Node tests passed after the runtime fixes.
- Five frontend scripts passed: preparation, handoff, captured forms, interactions and application statuses.
- All 13 focused Chromium form/attachment tests passed, including delayed select/checkbox/radio changes and repeated-fill acknowledgement.
- The final full extension suite passed all 82 tests in 187 seconds, including both shipped-ZIP/real-API/production-frontend export-and-attachment flows. No runtime edits followed this run.
- All 17 document endpoint tests passed, including real LibreOffice conversion, Markdown output, CV revision binding, strict input bounds, safe XML and no application-state writes.
- The broad backend run completed 697 tests with 25 skips and two LibreOffice timeout errors under host contention. Both affected modules were rerun separately: all three realistic-CV tests and all three template tests passed. The remaining tests in the broad run passed. Browser tests are deliberately enabled separately with `RADIUS_EXTENSION_BROWSER_TESTS=1`.
- Shell syntax and `git diff --check` passed.

Docker is unavailable on this host, so image builds, PostgreSQL container smoke tests and the Chromium production sandbox check remain CI gates. Do not treat local SQLite/browser results as proof of those container checks.

All filling and file-selection trials use fictional data and intercepted employer forms. No real application was filled, uploaded or submitted during this preparation. Embedded forms, CAPTCHA, consent, signatures and unsupported/custom uploads remain manual. Selecting a file on a real employer site can upload immediately; test rollout with a controlled form.
