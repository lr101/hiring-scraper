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
