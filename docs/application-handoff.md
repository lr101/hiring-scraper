# Saved CV and application workflow

## Prepare inside Radius

With a saved CV, choose **Prepare inside Radius** on the application page. Radius
uses the selected English/German base CV, saved career evidence and writing
preferences, the job description, additional instructions and any captured form
questions. The resulting CV, cover letter and answers open directly in the
existing review, PDF preview and download flow. There is no ChatGPT tab or result
file to import. Review all factual claims and wording before using the documents.

### Draft, review and revise

The workflow follows the drafting/review/revision pattern of
[ai-job-search's apply workflow](https://github.com/ai-job-search-dev/ai-job-search/blob/master/.claude/commands/apply.md):

1. Evaluate requirements against saved evidence while drafting the CV, business
   letter and captured answers. Missing permission/sponsorship, graduation or paid
   experience facts remain unknown.
2. Send the original sources and draft inline to a fresh, independent Codex context.
   It reports fit evidence, required/preferred gaps and concrete grounding, coverage,
   voice and formatting issues.
3. Revise the draft against that critique, preserving verified facts and the selected
   original CV layout. Validate both drafting passes against the locked history,
   structure and text budgets. A failed review or revision returns an error rather
   than publishing the first draft.
4. Compile safe fixed-template PDFs and inspect their page counts and extracted text
   with pypdf, including known email/phone details. The CV target is at most two
   pages and the letter target is one. Warnings remain visible; these checks do not
   prove visual layout, full ATS compatibility or claim accuracy. Custom uploaded
   templates retain their original source/assets and are marked as needing local
   compilation and inspection; they are never executed by this workflow.

**Fit and document checks** next to the returned documents shows the model's fit
assessment, the initial critique sent for revision and the final PDF checks. A
critique is not proof that the revision resolved every issue. Checks describe the
generated version; inspect again after editing or adding a photo. Review uses the
saved posting and evidence, with no external company-research tools. No application
status is advanced by preparation.

In the review editor, choose **CV** or **Cover letter** and describe a change in
**Refine your document**. Radius sends the selected current document, the request,
and the saved job/profile/CV context for one ChatGPT revision. The proposed version
and its review are kept separate until **Use this version** is selected. A changed
document, profile, CV or job invalidates the proposal; request a new one. Marked CV
revisions must preserve the original template and history. For custom CV templates
that Radius cannot render, inspect the proposed source before using it. The previous
fit and PDF checks describe the earlier version and must be checked again after edits.
Accepted revisions are saved to Radius before the editor changes. After a CV revision
or manual LaTeX edit the source and PDF are authoritative and readable CV text is not
automatically regenerated. Invalid
source edits stay unsaved until corrected.
The latest suggestion is kept in the private backend state directory for up to seven
days, survives a page reload, and is removed with the saved application. Review all
claims before using the result; no employer form is submitted.

Preparation uses **three Codex requests** from the connected subscription. Each is
ephemeral and isolated. The native calls share a 15-minute overall budget, with
at most 300 seconds per call; local PDF checks may take additional time. The page
polls a backend job, so reloading or closing it does not cancel preparation. It
shows the current stage and can recover the result when reopened. Cancel stops
queued or active model work. Retry reuses validated checkpoints only when the
request and source revision are unchanged.

The server prompt reflects the same original-template and matching-language example
letter policies as the companion. Marked profile/responsibility prose is editable;
headers, layout, commands, bullet count, order and original per-line budgets stay
locked. The saved cover-letter example guides voice/paragraphs, while the current
CV/profile supplies facts. Letters use a separate business-letter template and their
reviewed prose is the source used for export.

### Writing lessons applied

The [reference README's writing and verification lessons](https://github.com/ai-job-search-dev/ai-job-search#tips-for-better-results)
now guide both the direct workflow and the shared web/companion handoff:

- Supply evidence-rich role/project descriptions: problem, own actions, methods and
  verified results. Profile hints encourage this depth; metrics are used only when known.
- Show skills through real examples and distinguish paid work from study projects.
  Career goals guide direction, while example letters and writing samples guide voice.
- Use relevant, distinct evidence within each editable line's existing budget. Shorten
  repetition before useful evidence, including older examples that matter to the role.
  Locked history, bullets, fonts and geometry stay intact.
- Connect a specific employer need to a verified example and future contribution in
  the letter; avoid a CV recap, generic praise and unproved promises.
- Treat rendered pages and extracted text as separate quality checks. Inspect page
  breaks, contact readability and the letter closing; source-only documents stay unchecked.

The direct review supplies concise required/preferred keywords from the saved posting.
Radius filters out terms absent from that posting, checks any supporting excerpt against
the original CV or saved factual profile (excluding style samples, examples and goals),
and compares the terms with the actual CV PDF text. The report distinguishes a literal
match, a missing exact term and an unavailable check. It shows verified source excerpts
separately from the model's support assessment: finding a quote does not prove the
model interpreted it correctly. Synonyms and translations can explain a missing term.
These checks produce no ATS percentage and never add unsupported keywords to documents.

### Connect your ChatGPT subscription

1. In **Settings → ChatGPT subscription**, or on the application page, choose
   **Continue with ChatGPT**.
2. Open **Open ChatGPT sign-in**, sign in to the intended ChatGPT account and enter
   the displayed one-time code. Device code login may need enabling in ChatGPT's
   security settings or workspace permissions. The code expires after 15 minutes.
3. Return to Radius. It detects the completed login and enables **Prepare inside
   Radius**. **Manage ChatGPT usage** opens the account's usage settings.

Authentication and token refresh are handled by the official Codex CLI. Usage
comes from that account's Codex entitlement within its ChatGPT subscription;
account/workspace policies and usage limits apply. There is no API-key fallback.
Radius strips inherited API credentials, access-token overrides and alternative
gateway settings from its Codex child processes. The optional
`APPLICATION_AI_MODEL` selects a model; leaving it empty uses the Codex default.

The release image includes Codex **0.163.0**, installed from the locked
`runtime/codex` package and exported as native Linux x64/arm64 binaries. Node is
needed only during the image build. Compose preserves the isolated login in the
`radius_codex` volume at `/var/lib/radius/codex`; changing the container does not
require another login. Startup initializes this private volume for the app's
unprivileged UID before serving requests. **Disconnect ChatGPT** clears the cached login through
Codex. Account access can also be managed in ChatGPT settings.

Use this connection in a private, access-controlled personal Radius deployment.
The signed-in subscription belongs to the operator of that instance; everyone
with access to its preparation endpoints can invoke it. These endpoints inherit
the deployment's existing access controls. Credentials are kept in an owner-only
runtime directory, never in browser storage, frontend code, source control or
the release image. Radius does not reuse the developer's `~/.codex` login.

For local development, run `npm --prefix runtime/codex ci --ignore-scripts`, then
set `RADIUS_CODEX_BIN` to the absolute path of
`runtime/codex/node_modules/.bin/codex` when starting the API. A compatible system
`codex` is otherwise found on PATH. `RADIUS_CODEX_STATE_DIR` optionally selects
the protected storage directory (default `~/.local/state/radius/codex`). It must
be persistent and writable; never point it at a developer's existing Codex home.
If device login is unavailable, follow the official local-login/secure-cache
transfer fallback into `RADIUS_CODEX_STATE_DIR/home`, then reload the page.

Sources: [Codex authentication and headless login](https://learn.chatgpt.com/docs/auth),
[non-interactive structured output](https://learn.chatgpt.com/docs/non-interactive-mode).

Direct preparation sends the selected CV text, relevant saved profile fields,
job description and captured question schema through Codex. It has no ChatGPT
conversation history, memory or Canvas access. Each generation uses an isolated
temporary directory, an ephemeral session, an explicit JSON schema and a read-only
sandbox. Shell, file, browser, plugin and agent tools are disabled. A file lock
serializes native login, generation and disconnect across API processes. Each call
has a maximum 300-second timeout within the workflow budget; Radius waits for a busy
connection but does not retry failed model calls, although
Codex may reconnect its transport. Native ChatGPT/Codex data handling policies
apply to the signed-in account.

The server builds context from saved records, checks the profile/CV revisions,
and rechecks job/profile/CV context after generation. It validates the returned
package, inert letter/fixed-template LaTeX grammar, marked CV layout/history and captured-question
constraints. These checks cannot prove every generated claim, so applicant review
remains necessary. Failed, invalid or stale results preserve current browser
documents. The current native call holds the connection lease until it finishes or times out.
The backend stores the current job and finished package in the private `application-jobs`
directory under `RADIUS_CODEX_STATE_DIR`, so devices connected to the same instance
can recover it. Records older than seven days are removed during subsequent job
activity; server administrators can also remove these files. Editable drafts sync to
Radius with version checks and explicit conflict resolution. Deleting an application
removes its backend job, checkpoints and draft and the current browser copy; an
extension copy on another device may remain. Deleting the profile or CV removes its
saved application data from Radius. Originals and application stages are not changed. No employer
form is filled or submitted by preparation.

The application companion's existing question-capture, draft bridge and manual
result import use the same package format. Direct generation uses the existing
draft storage that the connected companion reads. The companion displays progress
and requires explicit review confirmation before filling or attaching a prepared
package. Questions captured after a job starts are not
included in that job; start a fresh preparation to include them.

Verification includes process-level CLI fixtures, an offline wire check using
the real pinned Codex binary, persisted-state permission checks under the production
UID, and the real UI/API/PDF path with synthetic login
and model responses. A real account sign-in and completed subscription inference
remain a separate live check requiring the account owner's approval in ChatGPT.

## ChatGPT handoff

1. Attach a CV to a profile and save it once.
2. Click **Start application** on a job. The remaining steps open on a dedicated page.
3. **Copy brief & open ChatGPT** includes the saved CV text, job description, employer/job URLs, the selected original LaTeX CV, a separate cover-letter template and the return format. Paste and send; the prompt asks ChatGPT to save the full CV and letter source in code Canvases, then create a return link containing those exact sources. A repeated CV attachment is unnecessary.
4. Click **Return to Radius with my documents** in ChatGPT; the documents open automatically in Radius. Download your finished documents; text editing is optional.

The job page contains one start link rather than an inline workspace. Profiles without an attached CV can use the browser-local text preparation flow or follow the direct attach link; **Save & continue** returns them to the saved-profile workflow. Multiple profiles can be selected when no profile was carried from the job. Language, extra instructions and recovery actions are under **Options**. The application page uses one back link and a short three-step indicator; repeated explanations and the global site navigation are omitted.

## Saved instructions, language and writing style

Each profile has **Custom application instructions** under **Application writing preferences**, limited to 5,000 characters. Use this for voice, language, letter length or achievements to emphasize. The value is stored in the profile preferences JSON, included in every new brief for that profile, preserved when older API clients omit the field, and cleared by explicitly saving an empty field. Older profiles read as empty. No schema migration is needed. Per-application **Additional instructions** override saved preferences on the same topic.

The handoff is a user prompt pasted into ChatGPT, written in English; Radius does not set ChatGPT's system message. English prompt text does not select the document language. The brief defines this order:

1. An explicit **Document language** selection overrides all language preferences.
2. With **Match the job posting**, use a language requested in this application's instructions, then the saved profile instructions.
3. Otherwise use the employer's explicitly requested application language, then the job description's dominant language. When those are unavailable, use the job title, then original CV language, and finally English.

This applies to CV headings and descriptions, the letter, answers and fallback phrases. Proper names and official qualifications retain their source spelling. The applicant's language proficiency is evidence rather than a writing preference. Profile and one-off instructions appear in a separate applicant-instruction section, outside quoted website/CV content, and cannot override accuracy, template constraints or the return format.

The cover-letter brief asks for a role-specific opening and verified examples that explain the applicant's contribution. It discourages interchangeable company praise, stock openings and CV retelling. A final editing pass adapts the [upstream unslop skill](https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md) to application prose in the selected language while preserving facts and personal voice. It must not invent anecdotes or emotions to sound human. These are generation instructions, not a guarantee about model output; no logged-in ChatGPT generation was tested.

## Reusable application context and fit assessment

**About me for applications** on the profile stores a career evidence bank (12,000 characters), writing samples (12,000), a full example cover letter per language (16,000), wording to avoid (3,000), career goals (5,000), and working preferences/dealbreakers (5,000). Draft these using the context in your usual ChatGPT conversation, verify the facts, and paste them into the profile. Save concrete examples with your contribution, outcome, dates and evidence links where available. Identify academic work as academic. Add two or three short pieces of your own writing to guide the voice.

Paste an example into **Example cover letter (English)** in the English profile-content tab and **Beispielanschreiben (Deutsch)** in the Deutsch tab. The matching application-language example guides letter structure, formatting and voice. Former employers, role names, addressees and claims are not copied as facts. Clearing one language leaves the other example intact; missing examples use the standard letter format.

These optional fields are included in copied briefs in both saved-CV and browser-local flows. Writing samples guide style rather than supplying new factual claims. The brief asks ChatGPT to choose relevant verified examples and avoid disclosing private dealbreakers in application prose. The data is stored in profile preferences JSON; no migration is required. Older clients that omit `application_personalization` preserve the saved object; explicitly sending an empty object clears it.

Choose **Assess job fit** in **Brief type**, then **Assess fit in ChatGPT**. The separate assessment compares skills, experience, qualifications, logistics, career direction and working environment. It asks for evidence, gaps, unknowns, dealbreakers, a recommendation and employer questions without an invented percentage score. It stays in ChatGPT; choose **Prepare application documents** to resume document preparation. The fit-assessment action does not call an AI API or retrieve ChatGPT history. Direct document preparation is described above.

## Original-template DOCX workflow

Use a DOCX upload to keep the source CV template. The app identifies text paragraphs in the original document and asks ChatGPT for edits to those paragraph IDs. Returned documents open with download buttons first and a PDF preview. Original paragraph text and further edits are available under **Edit document text**. The preview updates after editing pauses. PDF.js draws the actual exported PDF in Radius, with page navigation and a bounded canvas size for phones. It does not rely on a native PDF iframe or an external viewer. Rendering errors leave downloads available. CV downloads use the employer name, such as `rösberg-CV.pdf` and `rösberg-CV.docx`, with unsafe filename characters removed. Printed text documents also use the employer and document type in their window title.

The app edits the original DOCX package. It retains sections, tables/columns, margins, fonts, paragraph/run properties, headers, footers and other unchanged package parts. **Download editable DOCX** returns that edited document. **Download PDF** renders the same document through LibreOffice Writer; it does not rebuild the CV using the generic Markdown layout. The original uploaded file remains unchanged.

Longer text can reflow onto more lines/pages. Mixed formatting within a paragraph follows its existing run spans; shortening/rewording text can move inline emphasis. The web editor changes body text (including text in body tables); headers and footers are retained unchanged rather than exposed for editing. Fonts unavailable on the server can be substituted. The renderer includes Liberation fonts; exact reproduction of a particular user's Word installation is not promised.

PDF/TXT/TEX uploads provide readable CV text or LaTeX source, but do not contain an editable Word template. Their CV editor explicitly offers a simple-layout export and a link to replace the source with DOCX. It does not claim to preserve an arbitrary uploaded PDF's layout. Cover letters, answers and notes use a simple separate document layout. The bundled PDF viewer requires Node.js 22.13+ to build; runtime containers already build with Node 26.

## LaTeX source and PDF generation

For a returned CV or cover letter, choose **LaTeX · single column** in **PDF layout**. **Download PDF** compiles the finished text with LuaLaTeX; **Download LaTeX source** returns an editable UTF-8 `.tex` file. In browser-local preparation, both exports appear beside the existing PDF/DOCX buttons. LaTeX creates a new A4 layout, including complete edited DOCX body text and textual headers/footers when using paragraph edits. It does not reproduce Word tables, images or formatting. Existing DOCX/PDF exports retain their original behavior. The on-page preview shows the content; download the LaTeX PDF to inspect its compiled layout.

The approach is inspired by [ai-job-search's LaTeX CV workflow](https://github.com/MadsLorentzen/ai-job-search/blob/master/.claude/skills/job-application-assistant/05-cv-templates.md). Radius uses its own prose renderer and template, without requiring Claude, an API key, or the upstream moderncv class. It preserves selectable Unicode text and literal date ranges; it does not silently cut content to force a page count. Check pagination in the exported PDF.

Legacy exports generate escaped LaTeX from prose. Cover-letter Canvas sources and prose-derived CVs use the fixed shared preamble/ending in `hiring_scraper/app/latex_template.json`. Marked base CVs retain their original source template instead. Their source can be downloaded without execution after comparison against a saved base; server PDF compilation remains restricted to the fixed Radius template. Custom CVs require local compilation with their original assets. For fixed-template documents, before compilation, a body parser permits only escaped text, balanced groups/lists, headings, emphasis and paragraph/list commands. It rejects all other commands, comments, raw special characters, custom packages, external assets and changed preambles. Shell escape is also disabled, renders are time-limited and concurrency-limited, and temporary files are removed after generation. Disabling shell escape alone would not safely constrain arbitrary LuaLaTeX. PDF text extraction is checked before returning the file. Unsupported font characters produce a visible error rather than a PDF with missing text; use DOCX or change the font in the downloaded source. A missing compiler leaves `.tex` and existing exports available.

To compile downloaded source locally: `lualatex -no-shell-escape application.tex` (substitute its filename). Requires LuaLaTeX, fontspec, geometry and Liberation Sans. Debian/Ubuntu packages: `texlive-luatex texlive-latex-recommended fonts-liberation`.

## Storage and file handling

Original CV bytes and extracted text are stored in a separate `profile_cvs` table, one attachment per profile. Replacing/removing a CV or deleting its profile removes that attachment. Profile preferences and job matching do not contain the file/text. Profile lists expose filename/type/size/update metadata only. Download, text and export responses use `Cache-Control: no-store`.

Uploads support PDF, DOCX, UTF-8 TXT and LaTeX TEX, at most 5 MB and 100,000 extracted characters. Upload a `.tex` file under **Profile → Edit → Your CV → Attach CV** (or **Replace CV**), then save the profile. Its original bytes remain downloadable; its UTF-8 source is included in the ChatGPT brief as quoted data. Source uploads are never compiled, expanded, or used to read referenced files. Custom LaTeX CV classes are accepted as source; application tailoring preserves them for source download, and custom-template PDFs require local compilation. The browser-local preparation flow also offers **Import .txt or .tex** for source files under 100,000 bytes, followed by **Save CV**. PDFs are limited to 20 pages; password-protected or scan-only PDFs need a readable replacement. Extraction runs in a short-lived process with a timeout and resource limits on Linux. DOCX packages with external linked images/templates, embedded objects, macros, duplicate parts or custom XML entities are rejected. The normal single-workspace API access model applies; this feature does not introduce multi-user authorization.

The original CV is never overwritten by the generated documents. Optional returned LaTeX source is included in the same browser draft and follows its deletion/expiry rules. Rendering sends edits transiently to the app server for export. Generated drafts, partial pasted responses and brief preferences are saved in localStorage for this job/profile on this browser. **Clear browser draft** removes them. A draft expires after 7 days without changes; expired records are removed when this application is opened, and an open tab also clears its current draft at expiry or on return to focus. Browser code cannot erase storage while the browser is closed. Browser edits also sync to Radius with version checks; storage denial leaves export available with a visible warning. Delete saved application removes the backend job and draft as described above.

Starting/preparing/importing an application never marks it submitted. ChatGPT is not controlled, logged into or called through an API. The prompt uses relevant personal information already available in that conversation, project or enabled memory, as authorized by the user. It requests finished personalized documents without follow-up questions or approval. Unsupported or unresolved details are omitted instead of invented. The app does not retrieve private ChatGPT history itself.

## One-package return contract

The prompt asks ChatGPT to create code Canvases named `tailored-cv.tex` and `cover-letter.tex` using the selected original CV template and the separate supplied letter template. It then reads their latest exact source into optional `latex_documents.cv` and `latex_documents.cover_letter` strings inside the application package. The final chat message contains **Return to Radius with my documents**, while the Canvases remain available for editing. A Canvas ID, share URL or sandbox path is never substituted for source text. The normal prose/paragraph fields remain available for DOCX and readable previews.

The return link is a snapshot, not a live reference to Canvas. After a Canvas edit, ChatGPT must synchronize the readable document fields and generate a new link. Radius imports the source strings, restores them with the browser draft, selects the LaTeX layout, and uses those exact sources for `.tex` and LaTeX PDF downloads. Editing a CV or letter’s prose in Radius discards that document’s stale Canvas source and falls back to regenerating it from the edited prose; the other document’s source remains intact.

Canvas opening is a prompt request, not an integration that Radius controls. If Canvas is unavailable, the prompt requests downloadable `.tex` files with the same source included in the package, and a short explanation. If the latest Canvas source cannot be read, it requests a complete package file instead of an invented link. The package JSON and Python recipe stay out of the chat message. If it cannot generate a verified link, it should offer a downloadable application-package.json file. Only when code execution and file creation are both unavailable does it show one JSON code block. Manual controls are under **Import manually**. File import and paste share one disclosure. Pasting it into the package field immediately validates and opens the documents. **Paste & open documents** reads the clipboard when permitted; native Paste remains available when reading is blocked. File import accepts the same object as `application-package.json` (or text), at most 200 KB. Copying a whole answer with exactly one JSON block is supported too.

```json
{
  "format": "radius-application-v1",
  "job_id": 42,
  "profile_id": 9,
  "cv_updated_at": "COPY THE EXACT VALUE FROM THE BRIEF",
  "cv": [{"id": 7, "text": "Revised verified achievement"}],
  "cover_letter": "Complete letter, or Not requested",
  "application_answers": "Known answers and unanswered questions",
  "review_notes": "Personalized for this role."
}
```

For DOCX, `cv` is an array of edits to existing paragraph IDs, or `[]` for no edits. For PDF/TXT/TEX, it is complete CV text. The other fields are Markdown strings. `review_notes` is a neutral tailoring summary, shown only under Options; it is not an action checklist. Wrong job/profile/source metadata, duplicate or unknown paragraph IDs and incomplete documents are rejected atomically, preserving the current draft. Obvious drafting placeholders and applicant confirmation requests are also rejected on import and checked before PDF/DOCX/print/copy export, so unfinished output cannot be presented as ready-to-download documents. This validates completeness rather than asking the user to approve generated facts. Earlier four-marker replies remain supported via manual paste, but are no longer requested.

## Compressed return link

The prompt includes a tested Python recipe using standard-library zlib and base64url to produce `#applicationz=<compressed package>` for this exact application. The app inflates the data using native DecompressionStream, rejects invalid UTF-8/checksums/truncated input, and bounds decompressed output at 200,000 bytes. Existing `#application=<percent-encoded JSON>` links remain supported. The 24,000-character generation cutoff is an application safeguard, not a universal browser limit. Longer links fall back to file import; raising the cutoff alone does not solve ChatGPT link rendering or truncation. The prompt never asks the model to hand-encode or shorten the link, fetch it, or claim it has already sent the result.

The application receives the fragment, removes it from the address bar, validates the object against the current job/profile/CV, and opens the editor. Same-page hash navigation and a new tab both work. Documents are not sent in an HTTP request URL or query string. Compression is not encryption. The link still contains the private draft in the ChatGPT conversation and can be read by whoever has that link; it is not encrypted or an authentication credential. Do not share it. The final job-site URL is separate: this mechanism opens Radius’s review editor, not an employer form, and performs no submission.

Link-only output is requested by default, but a normal ChatGPT prompt cannot force tool execution or guarantee every response: ChatGPT availability, code tools, link rendering, safety redirects and output truncation are outside the app’s control. Actual logged-in ChatGPT link generation was not tested in this environment; direct access returned HTTP 403. Browser tests use a simulated ChatGPT destination and execute the actual prompt recipe against synthetic documents.

A guaranteed short opaque URL would require storing the result on Radius through an authenticated GPT Action/MCP/API call and returning a token. A plain conversation cannot store newly generated content server-side just by printing a link. No public upload endpoint or automatic submission was added.

Clipboard/popup failures have a manual fallback. The start button copies during the click before opening ChatGPT directly, so clipboard denial cannot leave an empty tab. If both copy methods fail, the brief is selected for Ctrl+C/⌘C. If opening a tab is blocked, an ordinary link remains available.

See [workflow options and experimental results](../reports/application-return-options.md) for Canvas, file-based returns, generated links, direct account integration and less conventional alternatives.

## Deployment and verification

PostgreSQL deployments apply migration `0028_profile_cv`; SQLite development uses the standard schema initializer. The production and CI Dockerfiles install LibreOffice Writer, Liberation fonts, LuaLaTeX and the recommended LaTeX packages. CI additionally installs Poppler for render-comparison checks. For local development, install `libreoffice-writer fonts-liberation poppler-utils texlive-luatex texlive-latex-recommended` alongside locked Python/frontend dependencies. DOCX download remains available when PDF conversion is unavailable.

- `tests/test_application_documents.py`: real LaTeX compilation and extractable Unicode/date text, safe literal TeX, DOCX header/body preservation, source downloads, missing compiler/font errors, stale source and unfinished-content rejection.
- `tests/frontend_application_canvas.mjs`: exact Canvas source survives the executed Python return-link recipes, both import paths and draft reload, with invalid source shapes rejected.
- `tests/frontend_application_personalization.mjs`: saved evidence/voice/goals/logistics in application and independent fit briefs, and bounded source selection.
- `tests/frontend_application_personalization_browser.py`: profile edit/reload, fit and application clipboard handoffs, exact Canvas source/CV/letter downloads, source preservation across reload, stale-source invalidation after editing, and mobile overflow checks on an isolated preview. ChatGPT is simulated.
- `tests/test_profile_cv.py`: persistence, replacement validation, profile isolation/deletion, stream size limits, original download, template export, stale source rejection and migration round trip.
- `tests/test_cv_realistic.py`: a fictional two-page professional CV; original and concise tailoring retain two pages, while deliberate longer/stress content reflows to three/five; compare both header/footer crops, formatting/ZIP parts, every paragraph and PDF word bounds. Reproduce the artifacts with `.venv/bin/python experiments/cv_formatting_trial.py`.
- `tests/frontend_application_ready_browser.py`: the realistic CV returns directly to downloads with editing hidden, PDF stays two pages, DOCX parts/source survive, bad imports preserve completed documents, and unfinished manual text cannot export.
- `tests/test_cv_template.py`: a styled, two-column DOCX fixture with colored heading, custom margins and header; verify all unchanged package parts and formatting survive edits; compare rendered page geometry, unchanged text positions/fonts and unchanged header pixels against the original rendered source.
- `tests/frontend_application_handoff.mjs`: source/return contract, saved-CV handoff, template change validation, malformed responses and safe simple-document HTML.
- `tests/frontend_application_return_browser.py`: actual fragment round trips and external link clicks, Unicode, no document data in HTTP URLs, source binding, draft preservation, local reload/new-tab recovery, one-block paste, file import, prompt recipe, expiry and mobile layouts.
- `tests/frontend_application_handoff_browser.py`: isolated preview workflow from attaching a CV through persistent reuse, application page, clipboard handoff, DOCX/PDF download, malformed return, responsive layout and no-CV attach-and-return.

The fixture is synthetic. These tests establish the supported template-editing mechanism, not fidelity to an unavailable personal CV. No real ChatGPT generation or employer submission is part of the tests.

## Description-only tailoring and letter format

The saved-profile, browser-local and extension prompts personalize the existing marked profile introduction (Kurzprofil) and work/project/study description prose. They freeze unmarked text, contact details, headings, skills lists, qualifications, role titles, locations, dates, entry order and layout. For marked base CVs, import and export validate all source outside responsibility/profile blocks and preserve commands, grouping, line slots and bullet count inside those blocks. Changing an underlying fact requires updating the base CV rather than tailoring it for one posting.

For a fixed-coordinate or custom-macro template, the formatting prompt moves only literal introduction/description text into zero-argument `\Radius...` macros inside their respective profile or responsibility blocks, leaving rendering commands at their original positions. Extraction reads these literal bodies without expansion. Replacements may not exceed the original macro's character count. This prevents unbounded expansion but does not measure glyph widths; inspect the compiled PDF. The original template, including image references, remains in the returned source. No arbitrary uploaded TeX is executed by the server.

The letter uses sender/contact information, recipient/company, date, role subject, salutation, three or four prose paragraphs, complimentary close and full name. Unknown address lines are omitted. The prompt excludes CV-style section headings and bullets from the letter. Use inert `\\` line breaks within contact/address blocks, `\par` between blocks and the exact `\thispagestyle{empty}` command to suppress the one-page letter’s page number. The restricted parser permits those literal commands without permitting arbitrary layout commands. Browser-only marked CV sources are compared with their local original on import and downloaded directly as `.tex`, without sending the source to the API.

Regression coverage uses synthetic CVs in `tests/test_tex_cv.py`, `tests/test_profile_photo.py`, `tests/test_application_documents.py` and `tests/application_companion/test_tex_cv.mjs`. `tests/frontend_profile_photo_browser.py` exercises profile photo persistence and real PDF/ZIP exports against an isolated preview. Browser checks cover photo/source-bundle compilation and language-specific letter references. Personal CVs, trial scripts and rendered artifacts remain in ignored local storage and are not shipped. These checks verify the source/return contract and rendering, not probabilistic adherence by a live ChatGPT session.

An optional CV photo can be attached in **Edit profile → CVs & writing** and saved with the profile. Its PNG bytes live in a separate profile attachment and are reused for English and German CV PDF previews/exports and source + photo ZIPs. The download step can exclude the saved image or use a temporary replacement held only in memory. Photo bytes are excluded from prompts, return packages and saved browser drafts. `PUT /api/v1/profiles/{id}/photo?filename=portrait.png` accepts raw PNG bytes (at most 2 MiB and 4096 pixels per side); GET returns the saved image and DELETE removes it. Profile JSON contains attachment metadata only. `photo_data_url` accepts a bounded PNG override; `use_profile_photo` defaults to true and can be set to false on application-document requests to omit the saved image. Photos apply only to CV LaTeX PDF and source + photo ZIP exports; `format=tex_bundle` returns source, the portrait PNG and local compilation instructions as a ZIP. Saved custom-template source is checked against the original before bundling, and browser-local source is validated in the browser before export. The server never executes custom TeX. Custom bundles currently require one PNG portrait reference with a plain basename; fixed-template PDFs place the photo beside the header without dropping its text. Cover letters omit the photo and use compact multiline sender/recipient blocks separated by paragraphs.


The existing CV introduction (Kurzprofil) may also be personalized. Mark it once with `% radius:profile` / `% radius:endprofile`, separate from work/study entries and with its heading outside. The parsers accept one optional inert profile block and do not treat it as career history. Fixed-position lines use literal RadiusProfile macros with the same layout and character-budget checks as description slots. Unmarked introductions, names and contact lines remain unchanged. Cover-letter generation uses a separate exact `letter` document-class preamble, stacked sender/location/contact details and no CV headline, Kurzprofil or photo. Older returned article-template letters remain importable.
