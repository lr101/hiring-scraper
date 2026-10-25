# Radius Application Companion

Prepare a CV for a discovered Radius job, follow its Apply link to the employer or ATS, and fill known details from your reusable profile. While browsing, use the popup to track a company and its job feed, or save one specific posting to a chosen profile. Filling is always started by you; the extension never submits an application or advances its application stage.

## Install in Chrome

1. In Radius, open **Settings → Application companion → Download Chrome extension**. The same ZIP and SHA-256 checksum are available in the **Container CI → Radius extension** artifact on a PR/main workflow run, and as assets on tagged GitHub releases.
2. Extract `radius-application-companion.zip`. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select the extracted `extension/` folder. Pin **Radius Application Companion**.
3. Have a saved Radius profile (create one in Radius once if needed). You can then edit its contact details, common answers and marked base CVs directly in the extension’s **Edit profile** page. Connected saves update Radius and sync back in the background. Keep unknown facts blank.
4. Under **Connect your Radius**, enter the Radius HTTPS address and click **Connect Radius**. Accept Chrome's request for that specific origin. If the API lives at another origin, enter it under **Connection details**. Sign in to Radius in Chrome when your deployment uses authentication.
5. Select your Radius profile, then use **Edit profile** to import English/German `.tex` files or paste their marked source and save. No Radius tab is needed for the API-backed workflow. The formatting prompt in the editor explains the required work/study markers.

The ZIP is unsigned and installed manually. To update, replace the extracted files in the same folder, click **Reload** in `chrome://extensions`, then reload the Radius and employer tabs. Export your reusable profile before changing install locations; Chrome can treat a different folder as another extension. Reuse the same folder to retain saved data.

## Daily flow

- Open the extension on the employer's job or linked application page. The popup shows only the current job. Other roles at the company and recent applications are not promoted into the main view. If a URL matches several roles, choose the current role explicitly.
- Choose **Prepare with ChatGPT**. The extension reads the current job and selected profile from Radius, chooses the English/German base CV, copies the complete brief, and opens ChatGPT directly. Paste the brief there. Your CV is not placed in a URL; opening ChatGPT alone does not send it. If copying is blocked, reopen the popup on the employer tab and copy from **Application brief**. Briefs expire after 30 minutes and are tied to job/profile/CV and saved facts.
- ChatGPT saves the marked CV and letter in code Canvases and returns a downloadable `application-package.json`. Return to the employer tab, reopen the extension, and choose **Import ChatGPT result → Import result file**, or paste the package/return link. A result for another job/profile/CV is rejected.
- Download the CV or letter as LaTeX PDF/source from the popup. Choose **Attach prepared documents** on a supported linked form to attach the generated files. Choose **Fill known details** to fill supported fields. The extension never submits the form.
- Use **Prepare from these questions** to capture the current form's question labels and constraints, then open ChatGPT directly with those questions included. Entered values are not captured.
- If navigation/login loses the job link, expand **Link this page to a job**, find the intended role, then explicitly choose **Use on this page**. Generic `/apply` pages require confirmation for each fill.
- Radius access stays in the background. The popup refreshes stale cached connections; saving a profile in the extension updates Radius. Connection setup, profile selection, base-CV imports, prompt preparation, result imports, and document downloads all have extension controls. Login to the Radius deployment may still require its normal sign-in page.
- On an unlinked page, **Track this company or save a job** remains available for adding that specific page. It stays hidden while a current job is selected.

## Data and freshness

Connected application details and CV text are copied from your selected Radius profile into trusted local extension storage, not Chrome Sync. Standalone contact/work history stays in local extension storage. The selected employer page receives those facts when you click Fill, and prepared document files when you click Attach prepared documents. Captured question schemas are stored for up to 20 job/profile pairs and copied only to the connected Radius origin. PDF/DOCX exports send prepared text or template edits to Radius for transient rendering; generated files are not persisted by this endpoint. Export contains personal information; keep it private. **Delete saved profile** removes the reusable profile and prepared extension packages; **Disconnect Radius** clears the connection, catalog, aliases and prepared packages and revokes its permissions.

CV files attached to a Radius profile are stored on the Radius server. The alternative browser-local CV text and all generated application drafts are stored in that Radius site's localStorage; they persist on this device and can be read by scripts on that origin. Use a trusted Radius deployment. Remove attached CVs in the profile editor; **Delete saved CV** clears a browser-local CV and its drafts. Clearing site data removes browser-local CVs and drafts, while attached profile CVs remain on the server. Deleting extension data does not delete Radius site data, and vice versa. Do not use fictional examples with real applicant facts.

Packages turn grey after seven days or when the selected profile, original CV version or reusable facts change. CV metadata refreshes while Radius is open; the current profile or browser-local CV version is checked again before tailored filling. A stale cached draft cannot silently rebind to edited reusable facts. The extension validates job/profile/version and conflicting contact emails; it cannot independently verify every generated claim. Review all generated text.

Catalog sync is bounded to 2,000 jobs, 2 MB of retained metadata, 20 pages and one minute. A limit is reported in connection settings. Opening the popup on an uncached public posting looks up its exact URL in Radius; **Link a discovered job** searches the server and preserves its active/newest ordering, with cached matches available offline. Lookup ignores marketing parameters, preserves meaningful queries and SPA fragments, and narrows by those references before examining at most 100 candidates for the same page path. If that limit is exceeded, choose the job through search. Lookup, save and import share the bounded cache; refresh preserves prepared/linked roles outside the sync window. Badge updates send only the hostname to Radius for live company/feed status; they never send the page path or query. Login/session and shared generic application URLs are excluded from automatic lookup. A job opened directly in Radius can still be prepared and imported by ID. The extension does not run background crawls; companies and feeds you add use the Radius server's scheduled workers.

Structured work histories include stable experience IDs, locations and known dates (`YYYY` or `YYYY-MM`). Missing months stay blank. Optional `cv_bullet_ids` connect selected CV paragraph edits to an experience; alternatively return `experience_bullets: [{"experience_id":"job-1","bullets":["Known accomplishment."]}]`. Original employment facts remain unchanged. Existing differing form values are preserved and reported.

## Production rollout

Deploy the API, frontend and downloadable extension from the same tested commit. The application image already includes LibreOffice Writer and Liberation fonts for PDF exports; a standalone API deployment needs those packages too. This change adds no database migration. Keep the deployment's existing access controls in place for CV and document endpoints.

After deployment, check `/health/ready`, download the extension ZIP and verify its SHA-256 checksum. Update the unpacked extension in its existing folder, click **Reload** in Chrome, and reload the Radius and employer tabs. With fictional data on a controlled form, verify question capture, the returned package, PDF/DOCX downloads and explicit file selection. Confirm that consent remains unchecked, no submit event fires and preparation does not change the tracked application stage. File selection may upload immediately on real employer sites, so use a controlled form for this check.

If rollout fails, redeploy the previous API/frontend image and restore the matching extension package, then reload both tabs. This slice has no schema change to reverse. Packages containing captured form answers require the new extension's binding checks; prepare a fresh package after rolling back rather than reusing those drafts.

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

### Live lookup and application profiles

The 2,000-row / 2 MB / one-minute catalog limit bounds the offline cache, not the jobs you can find. Opening the popup checks the configured API for the exact public job URL. Automatic toolbar badges check company/feed status using only the hostname, with a five-minute session cache. Company domains match any subdomain; supported ATS services with employer-specific hostnames match the full tenant hostname. Shared boards that identify employers in URL paths use explicit exact-job lookup only. The popup shows career feed links, a company job count and up to eight related jobs.

Select a Radius profile under Connection. Contact details, common answers (including salary), and saved CV text come from that app profile. Edit these in Radius and use Sync in the extension after changes. Profile labels are never assumed to be applicant names, and CV text is not used to guess contact details, employment dates or salary. Switching profiles replaces the synced facts so another applicant's details cannot carry over. Standalone mode retains its local editor.

### Adaptable field matching

Application details also accept confirmed salutation, birth name, full birth date (`YYYY-MM-DD`), birthplace, nationality, driving licence and reusable additional information. Edit these in Radius or the standalone extension profile, then sync. Blank facts remain for review; nationality is never derived from residence, and birth name is never copied from surname. Exact job-linked prepared answers remain available when the corresponding reusable fact is blank. On bewerber-online.de, Bootstrap question groups recover mislinked labels and datepicker fields receive the observed `DD.MM.YYYY` format. Readable frames without controls or nested frames (such as similar-job lists) do not count as missing application fields. Consent and submission remain manual.

Save marked **English and German base LaTeX CVs** in the Radius profile editor, or paste both sources into the standalone extension editor. Work and study records, including responsibilities, are extracted from non-printing markers instead of a separate profile editor. Use the formatting prompt shown beside the CV fields, or [the documented prompt](../../docs/cv-formatting-prompt.md). Keep the newest entry first and stable IDs identical across translations. Application preparation selects the matching base language; an explicit language choice overrides automatic posting-language detection. CV downloads and attachments use LaTeX PDF or .tex source only. Employer fields that accept only DOCX require manual handling.

Prefilled work rows can appear in a different order: the companion matches employer, title, known dates or a checked current-role control to saved jobs, and fills only when the assignment is unique across the rows. Empty rows receive the remaining jobs in saved order. Ambiguous or contradictory rows remain for review; an existing employer, title or known date can identify the intended job. Matching never invents missing months.

Work-history discovery recognizes complete semantic fieldsets, ARIA groups and nested ordinary cards beneath a work-history section. A card needs one company and one title field; generic “Name” additionally needs a start-date or current-role control before it can mean the company. List wrappers are not treated as single jobs. Reference and emergency cards are excluded, and applicant contact values do not fall back into unrecognized history or education sections. Dynamic changes invalidate discovered groups before reuse.

Contact matching combines associated labels, narrowly scoped adjacent labels, ARIA labels, `autocomplete`, placeholders and normalized `name`/`id` attributes. It understands camelCase and bracketed names (such as `candidate[address][postalCode]`), common English/German wording, required/optional markers and conversational prompts such as “Where do you currently live?”. “Name” means the full name; first/last-name fields use their specific profile facts. Contradictory recognized signals and questions about employers, references or consent remain unresolved.

Explicit email/phone confirmation fields can reuse a saved contact when there is one primary field. Multiple independent primary fields remain ambiguous. Plain containers with direct section headings receive the same reference/history checks as semantic groups. If a question's label or meaning changes while filling, later fields pause for a fresh review.

Native radio questions can use fieldsets, ARIA radio groups or application-question wrappers. Known text preferences match explicit options, including English/German employment terms such as Part-time/Teilzeit. Yes/No choices use saved explicit answers. Set **Willing to work on site** in Radius application details or the standalone extension editor; Unknown leaves the question unanswered. Existing choices are preserved, and duplicate or disabled matching options remain unresolved. Custom radio widgets still require manual entry.

This approach draws on [Bitwarden's multi-attribute identity matching and browser events](https://contributing.bitwarden.com/architecture/deep-dives/autofill/generating-fill-scripts/) and [Simplify's distinction between profile facts and saved question answers](https://help.simplify.jobs/help/articles/2415391-using-copilot-to-autofill-applications). Matching runs locally without a model request. Arbitrary employer questions still use the existing exact, application-scoped prepared-answer flow.

The browser regression suite exercises the shipped MV3 extension against fictional forms: English/German conversational labels, nested identifiers, contradictory hints, localized native choices, repeat fills, unknown preferences, disabled options, preservation of existing answers and editor persistence. Run it with the Development and verification commands above.

See [profile-first matching experiments](../../reports/autofill-lab/README.md) for the research, reproducible stress set and limitations of fuzzy, semantic and global assignment strategies. Experimental matchers are not imported into the extension runtime.

The [expanded corpus](../../reports/autofill-lab/expanded.md) evaluates 499 forms and 950 scored fields, excluding support controls. It covers varied label layouts, confirmations, unrelated questions, preserved values, native choices, mid-fill context changes and reordered/ambiguous work records. Results are synthetic regression evidence, not an estimate of real ATS accuracy.

Real-form layouts also support full work-history textareas, numbered career entries, latest-employer prompts and paired period / employer-and-duties fields beneath explicit career headings. These format the saved records in profile order, retaining year/month precision and using “Present” for current roles. Existing incompatible drafts are preserved; text is never truncated to fit a shorter control. Site-specific Add/Save editors still require manual completion.

When bare “Name” accompanies one applicant “Vorname” question, it uses the surname; explicit full-name wording and `autocomplete=name` keep their full-name meaning. Related questions can span nested question groups within the same form. Separate street and house-number questions remain for review when the profile only has a combined address line. Formatted history must satisfy native input constraints before it is written.

Contact matching handles bounded German required prefixes and single-question paragraph labels. Bare “Name” alongside one unambiguous “Vorname” means surname; explicit full-name prompts retain their meaning. Postal-code/city and full postal addresses are composed only when all components are saved. A split street/house-number form is left for review when the profile supplies only a combined address line.

See the [offline real-company evaluation](../../reports/autofill-real/improvements.md) for the fixed captures, before/after results and untested framework workflows.
