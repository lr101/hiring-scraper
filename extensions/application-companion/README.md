# Radius Application Companion

Prepare a CV for a discovered Radius job, follow its Apply link to the employer or ATS, and fill known details from your reusable profile. Filling is always started by you; the extension never submits an application or changes its application stage.

## Install in Chrome

1. In Radius, open **Settings → Application companion → Download Chrome extension**. The same ZIP and SHA-256 checksum are available in the **Container CI → Radius extension** artifact on a PR/main workflow run, and as assets on tagged GitHub releases.
2. Extract `radius-application-companion.zip`. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the extracted `extension/` folder. Pin **Radius Application Companion**.
3. Open the extension's **Edit profile** page. Enter and save your contact details, original employment records, known dates and common answers. Keep unknown facts blank. These facts must describe the same person as the Radius CV profile you select.
4. Under **Connect your Radius**, enter the Radius HTTPS address and click **Connect Radius**. Accept Chrome's request for that specific origin. If the API lives at another origin, enter it under **Connection details**. Sign in to Radius in Chrome when your deployment uses authentication.
5. Select your Radius profile. Open a job's **Start application** page in Radius. Use its saved profile CV, attach one to the profile, or save original CV text in the browser. Keep a Radius tab open while using tailored packages, so the extension can check its current CV version.

The ZIP is unsigned and installed manually. To update, replace the extracted files in the same folder, click **Reload** in `chrome://extensions`, then reload the Radius and employer tabs. Export your reusable profile before changing install locations; Chrome can treat a different folder as another extension. Reuse the same folder to retain saved data.

## Daily flow

- A grey dot and grey entry mean a discovered Radius job has no current prepared CV. Click **Prepare with ChatGPT** to open the job's Radius preparation page.
- Use the saved profile CV or save original CV text for the selected profile in Radius, then copy the prepared prompt and open ChatGPT. Review what you share. Radius makes no automatic ChatGPT request.
- Paste ChatGPT's JSON or full return link into the preparation page, or follow its return link. Wrong-job/profile/CV-version packages are rejected. Prepared CV, cover letter and answers can be reviewed and downloaded as text. The extension connects the saved draft automatically.
- A green **1** means an exact matching job has a prepared package. Higher numbers require choosing a job. A shared hostname alone never authorizes tailored answers.
- Open the toolbar on the listing before clicking **Apply**, or choose **Open application** in the popup. This grants temporary page access and tracks the employer/ATS URL back to the same job, including new tabs and redirects.
- If a login or wizard breaks the chain, use **Continue your application → Use on this page**, or **Link a discovered job**. Shared generic `/apply` pages require an explicit confirmation for each fill; confirmation is tied to the current document/form and consumed after one use.
- Click **Fill known details**, inspect unresolved fields, and repeat on later steps. Review and upload the CV yourself. The extension does not click Next, Review, consent, signatures or Submit. Employers may autosave entered fields.

Source URLs stay unchanged. Separate application aliases are kept per job. Meaningful queries and fragments matter; marketing parameters are ignored. Login/session URLs are never permanent aliases. English/German native fields and unambiguous semantic inline work-history groups are supported; custom employer modals, frames, shadow DOM and rich text need manual entry.

## Data and freshness

Reusable contact/work history is in local trusted extension storage, not Chrome Sync and not sent to Radius. The selected employer page receives those facts only when you click Fill. Export contains personal information; keep it private. **Delete saved profile** removes the reusable profile and prepared extension packages; **Disconnect Radius** clears the connection, catalog, aliases and prepared packages and revokes its permissions.

CV files attached to a Radius profile are stored on the Radius server. The alternative browser-local CV text and all generated application drafts are stored in that Radius site's localStorage; they persist on this device and can be read by scripts on that origin. Use a trusted Radius deployment. Remove attached CVs in the profile editor; **Delete saved CV** clears a browser-local CV and its drafts. Clearing site data removes browser-local CVs and drafts, while attached profile CVs remain on the server. Deleting extension data does not delete Radius site data, and vice versa. Do not use fictional examples with real applicant facts.

Packages turn grey after seven days or when the selected profile, original CV version or reusable facts change. CV metadata refreshes while Radius is open; the current profile or browser-local CV version is checked again before tailored filling. A stale cached draft cannot silently rebind to edited reusable facts. The extension validates job/profile/version and conflicting contact emails; it cannot independently verify every generated claim. Review all generated text.

Catalog sync is bounded to 2,000 jobs, 2 MB of retained metadata, 20 pages and one minute. A limit is reported in connection settings. A job opened directly in Radius can still be prepared and imported by ID. No background crawl or indefinite polling occurs when Radius is closed.

Structured work histories include stable experience IDs, locations and known dates (`YYYY` or `YYYY-MM`). Missing months stay blank. Optional `cv_bullet_ids` connect selected CV paragraph edits to an experience; alternatively return `experience_bullets: [{"experience_id":"job-1","bullets":["Known accomplishment."]}]`. Original employment facts remain unchanged. Existing differing form values are preserved and reported.

## Development and verification

Source: `extensions/application-companion/`. Runtime is dependency-free, MV3, with no required site-wide host access or remote code. Optional persistent permissions are requested only for the connected Radius origins. No site messaging API or `externally_connectable` entry is exposed.

```sh
npm --prefix extensions/application-companion ci
npm --prefix extensions/application-companion run check
npm --prefix extensions/application-companion test
python3 scripts/build_extension.py
# Uses the project's locked Python environment and full Chromium (not headless shell).
uv sync --locked
uv run python -m playwright install --with-deps chromium
npm --prefix frontend ci
npm --prefix frontend run build
RADIUS_EXTENSION_BROWSER_TESTS=1 uv run python -m unittest discover -s tests/application_companion -t tests -v
```

The builder uses an explicit runtime allowlist, fixed ZIP timestamps/modes and a SHA-256 file. Private profiles, POC captures, tests, simulator servers and node_modules cannot enter the archive. The `Container CI` workflow tests the shipped extension in Chromium, builds this ZIP on every PR/main/tag, uploads an installable artifact, gates image publication on that job, and attaches the package/checksum to tagged releases. Runtime and frontend share the same commit.
