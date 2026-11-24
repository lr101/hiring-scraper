# Prepare application documents

Open **Prepare application** on a job and select a profile. Attach a marked
English or German LaTeX CV in the profile. On first use in a language, review
the imported base CV and choose **Confirm and save base CV**. Radius stores the
structured document and projects its career facts for matching. The history
IDs, employers and dates must agree across translations.

Connect a subscription through **Continue with ChatGPT** on the application page
or in Settings. Radius uses the bundled Codex runtime and the connected
account's entitlement. Device sign-in can require permission from the account
or workspace. The device code expires after 15 minutes.

**Prepare documents** creates a CV and then a cover letter. **Full** mode also
reviews the pair. **Fast** skips model review. The worker keeps completed
stages across retries. Open the page again to check progress and review any
documents already published.

Check the evidence behind claims and review findings in the editor. Style checks,
such as repeated openings or phrases, surface as suggestions in both modes and
give Full review targets for small wording edits. Edits
save automatically. If another tab or the worker saved a newer version, the
editor keeps your text and offers to reload or overwrite. **Improve with AI**
requests an optional replacement for one unit. Use **Version history** to
restore an earlier version and **Download PDF** to render the currently saved
version. CVs use the shared print layout with an optional saved portrait. When
Radius flags changed inputs, regenerate to update the tailoring. Radius leaves submission to you.

The companion fills saved profile facts after you start a fill, and can
capture the current form's question labels and constraints. Download v2 PDFs
in Radius and attach them on the employer's form. Existing v1 packages retained
by the companion can still be used through its legacy package tools.

## Data and deployment

- Radius sends the selected base CV, relevant profile preferences and job
  context to the connected Codex runtime. Voice notes and language-matched
  style examples guide wording. Profile photos are used by the local renderer.
- `RADIUS_CODEX_BIN` can point a local API or worker to the pinned Codex binary
  from `runtime/codex`. `RADIUS_CODEX_STATE_DIR` selects its private persistent
  state directory. Compose mounts the state volume at `/var/lib/radius/codex`.
  Compose binds to loopback by default. The API gives each client with access
  to Radius the full workspace and use of the connected subscription. Use
  an authenticated proxy or SSH tunnel for remote access.
- The structured documents and verified PDFs are stored in PostgreSQL. Radius
  keeps up to 20 versions per job, profile and document kind. A pending download
  displays render progress and can be retried after a failure. Removing the
  base CV also removes these documents, their render cache and queued work.
- The worker needs Chromium and the built `frontend/dist/print.html` entry
  for PDF rendering. See the architecture guide for queue and worker details.

## Recover a v1 working draft

Before starting v2, stop the v1 API and preparation worker so all saved files
stay stable. With the v2 image configured, export each profile that has legacy
edits. Replace `1` with its Radius profile ID and run this in a private directory:

```sh
umask 077
docker compose run --rm --no-deps -T --user 10001:10001 --entrypoint python app \
  -m hiring_scraper.app.legacy_application_drafts 1 > radius-v1-drafts-1.zip
```

The ZIP includes saved working drafts and revision records from the private
Codex volume. Use the job ID in each filename to locate the draft. Copy any
edits you want to keep into the corresponding v2 editor after preparation.
Keep the archive private. Removing a profile or its base CV also removes its
legacy records from the Radius volume.

## Private evaluation

The developer harness `scripts/eval/application_documents.py` accepts inputs and
writes artifacts only under the ignored `data/private/` directory. It uses an
authenticated evaluation runtime under `data/private/codex/` by default.
Set `RADIUS_CODEX_STATE_DIR` to another directory inside `data/private/` to
reuse an isolated evaluation connection.

## PDF export

The preparation worker uses sandboxed Chromium with the Compose seccomp and
AppArmor settings documented in the README. PDF export requires an extractable
text layer, the applicant's name and contact lines, and readable glyphs.
Pagination, reading order and overflow checks are advisory. The renderer prints
one document at a time, with a 192 KiB structured input limit and a 12-page
hard cap. The recommended limits remain two pages for a CV and one for a letter.

## Roll back the application image

Stop Radius before switching versions and leave the volumes in place. With the
**v2 image still configured**, start just the database before running the rollback:

```sh
docker compose stop
docker compose up -d --wait db
docker compose run --rm --no-deps migrate --rollback-application-documents
```

Then pin the previous application image and start the stack. The rollback returns
Alembic to revision 0043 and preserves the v2 document, PDF and queue tables. The old
worker has its own queue. To return to v2, stop the application containers again,
restore the v2 image pin and start the stack. The migration reuses the preserved
tables. Use this procedure for rollback to 0043.
