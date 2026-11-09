# Application generation integration

## Prepared in the application code

The application-preparation worker reads `application_prompt.txt` through
`application_generation._prompt`. Drafting, independent review, revision,
structural correction and final content review receive that shared context.
Marked macro CVs additionally receive character budgets calculated from the
original editable macro bodies, with an 85% recommended target. Existing
`tex_cv` validation remains authoritative: prompt guidance does not relax the
original contact, title, date, template, formatting or length locks.

The prompt asks for grounded, distinct role examples, compact cover letters,
honest gaps and source-conflict handling. Title-only contexts may guide applicant
example selection and intended contribution, but cannot establish employer duties.
The independent-review task also distinguishes source discrepancies from actual
unsupported draft claims. Final grounding findings still reject the package.
These instructions guide the model; unit tests cannot establish prose quality or
prove that every model output follows them.

The Dockerfile copies the application package, including the prompt. No schema,
frontend, provider setting or subscription-limit change is needed. Existing saved
documents are not rewritten. Previously saved preparation checkpoints may contain
an earlier draft/review; start a fresh preparation to use the updated instructions.

Offline evaluation tooling is maintained separately under
`experiments/application_hillclimb/`. That directory is excluded from Docker build
contexts and is not part of application startup, package installation or the
deployment's application test discovery.

## Profile enrichment boundary

The supported profile path is `GET /api/v1/profiles` followed by
`PUT /api/v1/profiles/{id}` with a validated `ProfileRequest`, or the existing
profile editor. The save path normalizes preferences and invalidates affected
matching scores. It is not a generic JSON merge-patch endpoint: preserve the
current request fields and localized content when applying a targeted enrichment.
English/localized fields are synchronized by the save path; generation selects
the requested language's saved content.

Before applying private attested evidence, identify exactly one profile against
the original CV and private source provenance. Preserve unrelated preferences,
contact details, employment identity and dates, language levels, attachments and
templates. Keep a private before/after snapshot, source hashes and fact-ledger
references outside Git; verify the saved readback and unchanged attachment hashes.
Do not replace a profile wholesale with a benchmark fixture or seed private facts
into application defaults.

At initial integration inspection, this worktree had no configured `DATABASE_URL`, no
default `hiring.db`, and no running application process with this worktree as its
working directory. No live profile was identified or modified. Other worktree
instances are not evidence of the intended deployment target.

## Prepared versus shipped

This is a source integration, not a deployment record. Initial preparation did
not publish an image, pull an image, restart a production service or verify a live
production application. A later commit, push or merge does not establish those
steps. The normal release/deployment path is documented in
[CI and releases](ci.md) and [Compose deployment](../README.md#compose-deployment).
Record the published image digest, running service image and health, and a fresh
preparation's provenance before claiming deployed equivalence.

During development, focused offline tests are appropriate. Full quality, browser
shards, PostgreSQL and container checks are reserved by `AGENTS.md` for immediately
before a requested push. Mock-provider generation tests consume no subscription requests
and do not establish live-provider or rendered benchmark success.

No ATS or deployed-equivalence claim follows from these source changes.
