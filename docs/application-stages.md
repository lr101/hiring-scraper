# Parallel application stages

The Journey map groups alternatives at the same checkpoint:

| Checkpoint | Main stage | Alternative |
| --- | --- | --- |
| 2 | Interested (`open`) | In GOD's hand (`in_gots_hand`) |
| 3 | Waiting for reply (`waiting_for_reply`) | Automated application (`automated_application`) |

New and Interested offer **In GOD's hand** for overqualified roles. Automated application
is assigned by the plugin's successful-submission callback. It can
move to Interview or Rejected. Existing swipe directions are
preserved; swiping right in In GOD's hand marks a manual application sent, and
swiping right in Automated application logs an interview. Undo returns to the
previous stage.

The proof-of-concept browser plugin is not present in this repository. Its form
submission-success callback can use the existing profile-specific status API:

```http
PUT /api/v1/profiles/{profile_id}/applications/{job_id}
Content-Type: application/json

{"status":"automated_application"}
```

Send this only after a successful application submission using autofill, using
the job and profile that started the application. Filling alone must not write
this stage. On failure, keep the previous stage. Repeated saves update the
same record. The status appears in Journey after refresh and is available in
both application-filter APIs. This endpoint records progress; it does not fill
or submit a form itself.

Both Automated application and Waiting for reply count toward weekly and
lifetime application momentum. Switching between these stages or repeating a
submission-status save never counts the same application twice.

Migration `0029_parallel_application_stages` expands the database constraint.
Local SQLite startup upgrades the old constraint while keeping existing records,
indexes and undo history. Downgrading maps In GOD's hand to Interested and
Automated application to Waiting for reply, including previous-stage values.

## Closed postings

When a complete source scan confirms that a posting disappeared, its Journey
state changes as follows: Accepted and Rejected stay unchanged; Waiting for reply
and Automated application move to Rejected; every other stage moves to Closed. Closed appears under **Past outcomes** and can be filtered there.
The previous stage is retained in history, and repeated scans do not change the
outcome again. If the posting reappears, its saved Journey outcome remains.

Migration `0030_closed_application_stage` adds Closed and applies these rules to
existing inactive postings. Local SQLite startup performs the same upgrade once.

## Background matching

Production source ingestion stores coalesced invalidations in `match_dirty_jobs`
in the source transaction. The profile match worker computes scores without job
or profile row locks, then tries the vacancy identity union guard
`(85793011, 6)`. If another identity writer owns it, the page rolls back and
retries from its durable queue/checkpoint on the next worker pass. It indexes
identities before locking affected jobs and profiles to verify sources and
publish a bounded page. Already retained identity keys are checked in batches.
A changed
source or cached assessment rejects the page; its invalidation remains queued.
Acknowledgements include the invalidation revision so a newer event is never
removed by an older worker pass. Existing saved stages remain available.

Profile backfills commit at most 100 jobs per step. `profile_match_progress`
stores the target matching version, job-ID cursor and initial high-water mark;
new arrivals and later source updates are handled by the dirty-job queue. Stale
profiles rotate between steps. Profile preference changes restart their scan;
manual rematch and refreshed profile vectors reset the checkpoint even when the
target preference hash is unchanged. Publication checks both the source snapshot
and the persisted checkpoint before advancing. Only automatic untouched New
entries can be retracted by changed matching results.

While work is pending, Journey returns `matching_pending: true`, uses persisted
ranks for sorting, and computes current card details only for the requested page.
The client continues polling until background work finishes. Unfinished profile backfills
have priority over semantic inference, AI selection and automatic preparation.
After backfill, one dirty batch runs before each regular inference cycle so
continuous ingestion cannot starve inference. SQLite keeps synchronous source/manual refresh compatibility.

Apply migration `0037_journey_match_progress` before starting updated PostgreSQL
processes. It adds the queue/checkpoint tables and requests one bounded
reconciliation of existing profiles. Tables inherit the application's default
migration grants; no new sequence privileges are needed. SQLite startup adds the
tables with `create_all`. Run the profile match worker continuously: queued
invalidations and checkpoints survive restarts, but cannot progress while it is
stopped. Normal job search snapshot invalidation keeps its global generation;
Journey page publication verifies only its batch, so unrelated ingestion cannot
restart a whole inventory scan.

### Lock ordering and rollout

Vacancy identity writers (background pages, search-copy linking, manual
tracking, and historical deduplication) share transaction guard `(85793011, 6)`
so concurrent unions cannot split retained groups. Acquire it before job or
profile row locks. Background pages always try without waiting, including when
there are no profiles. Manual tracking commits identity indexing in its own
transaction before taking the job and profile locks for a saved-stage change.
Search-copy linking acquires the guard without taking profile row locks.
Historical deduplication indexes before reserving profiles. An explicit full
PostgreSQL rebuild follows identity guard, source jobs, then profiles; the API
uses that legacy synchronous rebuild only for SQLite.

The search-generation fence `(85793011, 2)` belongs only to the final
source/evidence commit hook: finish domain writes, acquire the fence, allocate
and prune the generation marker, then commit. Keep this short fence even though
the identity guard is separate. Sequence values alone do not follow commit
order; allowing an earlier, lower marker to commit after a higher marker would
leave cached searches unaware of that source change. Heavy identity union and
match publication never hold this fence. Writers of the same source or saved
profile can still wait for each other; unrelated source commits can proceed
while an identity transaction is open.

This repair changes the identity advisory key. **Stop all old application and
worker processes that can write identities before starting any updated
processes.** Drain their transactions, then restart the complete application
and workers together; do not use a rolling deployment or hot reload across
versions. Old and new identity guards do not coordinate. This repair adds no
schema migration beyond the existing `0037_journey_match_progress` prerequisite.
Queued revisions and profile checkpoints survive the coordinated restart.

For a read-only blocking check, use a monitoring connection and inspect process
IDs, transaction ages and blocking edges without logging SQL or profile data:

```sql
SELECT pid, clock_timestamp() - xact_start AS transaction_age,
       wait_event_type, wait_event, pg_blocking_pids(pid) AS blocking_pids
FROM pg_stat_activity
WHERE datname = current_database()
  AND (wait_event_type = 'Lock' OR cardinality(pg_blocking_pids(pid)) > 0)
ORDER BY xact_start;

SELECT pid, classid, objid, granted
FROM pg_locks
WHERE locktype = 'advisory' AND classid = 85793011
ORDER BY objid, granted, pid;
```

Key 2 should appear only around commits; key 6 may span identity indexing and
page publication. Worker ownership is key 4, and LLM process ownership is key 5.
