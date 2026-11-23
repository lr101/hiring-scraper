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

Check the evidence behind claims and review any findings in the editor. Edits
save automatically. If another tab or the worker saved a newer version, the
editor keeps your text and offers to reload or overwrite. **Improve with AI**
requests an optional replacement for one unit. Use **Version history** to
restore an earlier version and **Download PDF** to render the currently saved
version. When the saved inputs change, choose **Regenerate** to update the
tailoring. Radius leaves submission to you.

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
  Use a private Radius deployment with its existing access controls. An
  authenticated connection can invoke the server's connected subscription.
- The structured documents and verified PDFs are stored in PostgreSQL. Radius
  keeps up to 20 versions per job, profile and document kind. Removing the
  base CV also removes these documents, their render cache and queued work.
- The worker needs Chromium and the built `frontend/dist/print.html` entry
  for PDF rendering. See the architecture guide for queue and worker details.
