# AI task fixes

- [x] Remove the built-in 50% AI quota threshold (subscription exhaustion still pauses matching).
- [x] Handle plain-text failures when saving Not Interested without a JSON parser error.
- [x] Add Match all jobs with AI again to AI matching status.
- [x] Investigate CV review timeout budgets and increase them.
- [x] Complete independent review and address rematch and repair-budget findings.
- [x] Pass host quality checks, frontend build and disposable PostgreSQL checks.
- [x] Finish both browser shards before pushing.

Publishing and merging are tracked in the session todo list.

## Investigation

The status client unconditionally parsed every response as JSON. A plain-text
500 response therefore hid the server failure behind Unexpected token 'I'.
The client now displays an actionable save error. All 38 existing backend
application regressions pass locally; the specific production 500 has not
been reproduced, so its server-side cause remains undetermined.

CV preparation runs assessment, draft, optional repair, independent review,
revision, and final review sequentially. Each runtime invocation was capped
at 300 seconds and all passes shared 900 seconds. Later review could receive
only the remaining budget. Runtime turns now allow 600 seconds and the workflow
allows twelve full turns (7200 seconds), including correction and repair paths. Cancellation and checkpoint recovery
remain available. Production timing logs were not available; budget starvation
is a code-level finding, not proof of the reported production timeout's cause.

Rematching clears saved AI assessments across profiles. The existing worker
recomputes eligible active jobs above the local base-score gate of 35; local
estimates are displayed while assessments are pending.


## Verification

- Final host quality check: 1,125 Python tests passed (79 opt-in tests skipped);
  configured frontend/extension Node checks, actionlint and ShellCheck passed.
- Production frontend build passed.
- Disposable local PostgreSQL concurrency regressions and migration graph passed.
- Browser shards: 79 and 78 tests passed; one optional private-CV test skipped in shard 1.
- Isolated preview: Not Interested saved with HTTP 200 and status
  not_interested; rematch POST returned HTTP 200.
- Docker is unavailable. Container builds, image/security checks and the
  container PostgreSQL smoke behavior have not been verified here.
