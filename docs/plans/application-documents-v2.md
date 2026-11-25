# Application documents v2: implementation plan

Status: implemented. Replaces the fixed-slot LaTeX tailoring pipeline
(`application_generation.py` macro edits, `tex_cv.py` budgets, `application_prompt.txt`)
with structured documents, one shared HTML layout, inline editing and a short, resumable,
grounded generation pipeline.

## Why

The former pipeline asked the model to rewrite LaTeX macros that are fixed-width justified
lines at absolute page coordinates, with budgets counted in characters. Characters are not
widths, so accepted edits can overflow or stretch, valid prose gets rejected, the model writes
to line lengths instead of for the reader, and custom CVs are never compiled during
generation. A chain of 4–9 model calls without retries, a final review that can discard a
finished package, and a 178-line prohibition prompt make preparation fail often and read
stilted. See the validation notes in the PR description.

## Decisions

- Documents are structured JSON (`CvDocument`, `LetterDocument`). Identity facts are locked
  typed fields that no model schema contains. Prose lives in addressable units.
- One React view renders documents. The inline editor and the PDF use the same component;
  the worker prints a built `print.html` in Playwright Chromium. Fonts are bundled.
- Generation: CV call, then letter call with original evidence grouped by employer, then one
  review call (findings plus patches, receives overflow telemetry). Fast mode skips review. Form answers stay a
  separate optional call. No AI requirement assessment call.
- Deterministic grounding produces unit-anchored findings. Unsupported qualifiers and
  high-severity grounding issues can trigger a retry. Editorial findings are review targets.
- One PostgreSQL queue with retries, stage checkpoints, short fenced leases and partial
  publication. The file/thread job store is retired once nothing reads it.
- The manual ChatGPT copy/paste handoff is removed. The connected ChatGPT integration runs
  at its default model, effort and service tier.
- Defaults: A4 (Letter selectable), PDF export only, Full mode default with Fast available,
  serif letter matching the CV, ~260–320-word letters in 4–5 paragraphs.

## Document contract (`radius-cv-v2`, `radius-letter-v2`)

Shared by Python (`hiring_scraper/documents/schema.py`, pydantic strict, `extra='forbid'`)
and TypeScript (`frontend/src/documents/types.ts`). All text is plain Unicode; no markup,
no LaTeX. Rendering escapes. Limits: unit text ≤ 1200 chars, ≤ 60 units per section.

```jsonc
Unit {
  "id": "u-…",                 // stable, unique in the document, [a-z0-9-]{1,64}
  "text": "…",                 // plain text, no newlines
  "lead_in": "",               // optional bold prefix rendered before text ("Studio Project:")
  "origin": "source" | "ai" | "user",
  "sources": ["cv:work-1:b2", "evidence:3"],   // evidence ids, may be empty for origin=source/user
  "role": "",                  // letter paragraphs: motivation|experience|education|bring|close
  "hidden": false,             // hidden units are kept but not rendered
  "original_text": null,       // exact pre-rewrite body, without the retained lead-in
  "ai_edited": false,          // user-edited wording that started as an AI change
  "reviewed_text": null        // accepted wording, cleared on edit
}
CvDocument {
  "format": "radius-cv-v2", "language": "de" | "en", "template": "classic-serif-v1",
  "page": "a4" | "letter", "photo": true | false,  // legacy field; show the profile portrait when available
  "header": { "name": "…", "contacts": [{"kind": "phone|email|location|link", "text": "…", "href": ""}] },
  "review_sources": {"cv:work-1:b2": "…"},  // cited source snapshots for this document version
  "headline": Unit,
  "sections": [Section]
}
Section {
  "id": "s-…", "kind": "profile|experience|education|skills|list|text", "title": "…",
  "group": "",                   // optional major heading shared by consecutive subsections
  "layout": "paragraphs|bullets|inline|columns",   // units presentation
  "units": [Unit],              // profile paragraphs, skills, list items, free text
  "entries": [Entry]            // experience/education only
}
Entry {
  "id": "work-1",               // history id from the marked CV (matcher identity)
  "kind": "work" | "study",
  "locked": { "company": "…", "title": "…", "location": "…", "start": "2022", "end": "2023", "current": false },
  "label": "",                  // optional small heading line above the entry (e.g. "INTERNSHIP")
  "intro": [Unit],              // paragraphs before bullets (degree notes, "Selected projects:")
  "units": [Unit]               // bullets
}
LetterDocument {
  "format": "radius-letter-v2", "language": "de" | "en", "template": "classic-serif-v1", "page": "a4" | "letter",
  "review_sources": {"job": "…"},
  "sender": { "name": "…", "lines": ["…"] },        // locked, from profile
  "recipient_lines": ["…"], "place_date": "…",
  "subject": Unit, "salutation": Unit, "paragraphs": [Unit], "closing": Unit, "signature": "…"
}
Finding {
  "id": "f-…", "document": "cv" | "letter", "unit_id": "u-…" | null,
  "code": "unsupported_number|unsupported_term|misattributed|sensitive_fact|placeholder|language_mix|repetition|overflow|extra_page|review_grounding|review_coverage|review_style|review_format",
  "severity": "high" | "medium" | "info", "message": "…", "suggestion": "",
  "claim": "",                 // exact affected wording when available
  "replacement": ""            // complete replacement for the unit, when offered
}
```

Evidence ids: `cv:<entry_id>:b<n>` (base bullet), `cv:<entry_id>:i<n>` (intro),
`cv:profile:p<n>`, `cv:<section_id>:u<n>`, `skill:<slug>`, `evidence:<n>` (career evidence
lines), `profile:<field>` (structured profile facts), `job` (statements about the role).

## Packages and files

Pure, DB-free (`hiring_scraper/documents/`):
- `schema.py` — models above, `walk_units`, `unit_by_id`, `apply_patch(doc, unit_id, text, origin)`.
- `import_tex.py` — marked Radius LaTeX → `CvDocument`. Supports the Radius marker family:
  `% radius:profile|entry|responsibilities|end` blocks, fixed-coordinate page-1 `\put` lines
  (joins line-fragment macros per bullet using the bullet glyph sequence), and flowing pages
  (`\CVSection`, `\CVSubsection`, `\CVRow`, `itemize`/`\item`, minipage lists, `\textbf`
  lead-ins). Headline, contacts, skills, competencies, languages and awards become sections.
  Unknown commands raise `ImportError` with a line number; never execute TeX.
- `project_tex.py` — `CvDocument` → flowing, marked LaTeX for matcher compatibility. Invariant:
  `career_facts_from_tex(project_tex(doc)) == facts_from_document(doc)`; for an unedited import
  it also equals the facts from the original source.
- `evidence.py` — builds the evidence pack (ids → text) from base `CvDocument` + profile.
- `grounding.py` — deterministic checks → `Finding`s (numbers incl. de/en formats, terms and
  tools against cited sources + profile skills, employer attribution, sensitive facts, placeholders,
  language mix, repetition).
- `text.py` — plain-text projection (ATS/readable text, letter text).

App (`hiring_scraper/app/`):
- `application_pdf.py` — Playwright Chromium render of `frontend/dist/print.html` with the
  document JSON (+ optional photo data URL), all network aborted, returns PDF bytes and
  telemetry `{pages, overflow_px_by_page, clipped_units, text}`. Checks pypdf and pinned
  PDFium extraction for readable text, word boundaries and control characters, and records
  normalized extraction parity. Keeps short hyphenated compounds together for extraction.
  Bounded semaphore; one lazily started browser.
- `application_pipeline.py` — stages `cv`, `letter`, `review`, `answers`; prompts in
  `hiring_scraper/app/prompts/`; injectable `generate(prompt, schema, **options)`; per-call
  retries; patch application rules; returns documents + findings + telemetry.
- `application_documents_v2.py` — storage + router (`/profiles/{id}/application-documents`):
  list/get versions, save with `expected_version`, export PDF, revert unit, start/cancel
  preparation, unit revision requests.
- Queue (`application_preparation_queue.py`): `record.kind` = `prepare|revise`; `attempts`,
  `next_attempt_at`; lease 6 min renewed (throttled 60 s) from `Progress.cancelled()`; fencing
  on id + token; typed `error_code`; checkpoints per stage keyed by input digest.
- Migration: `application_documents(profile_id, job_id, kind, version, body JSONB, origin,
  stale_inputs, input_digest, created_at)`, PK `(profile_id, job_id, kind, version)`, keep 20.
  Base CV structure in `ProfileCV.variants[lang].document` next to the projected `text`.

Frontend (`frontend/src/documents/`):
- `types.ts`, `CvView.tsx`, `LetterView.tsx`, `classic-serif-v1.css`, fonts in
  `frontend/public/fonts/` (Liberation Serif, SIL OFL), `print.html` + `print.tsx` (second Vite
  entry, reads `window.__RADIUS_DOCUMENT__`).
  The shared CV view measures inline skill rows and hides separator bullets across wraps
  while keeping a real space in the PDF text layer so extracted items stay separated. The
  editor resyncs on edit or resize, and print resyncs after fonts load and layout changes.
- `DocumentEditor.tsx` + `DocumentReview.tsx` — renders the view with
  `contenteditable="plaintext-only"` per unit, Enter splits a bullet, Backspace on empty
  removes, hide/restore, undo/redo, debounced autosave with `expected_version`, findings
  badges, page guides + overflow indicator, "Download PDF". The inline review panel steps
  through visible AI changes in document order, compares current wording with versioned
  `review_sources` citations, shows a word diff against `original_text` for rewrites, and
  presents unit findings with actionable replacements when available. Accept, edit, restore
  and apply-suggestion actions share the same undo history and versioned autosave.
  Reviewed-state persistence stores acceptance in the document JSON (`reviewed_text = text`),
  so reviewed progress survives reloads and version history; any edit clears `reviewed_text`
  (marking `ai_edited: true` while keeping `original_text` and `sources` for another pass),
  and restoring original wording resets the unit to `origin: "source"` and clears review
  fields. Alt+Shift+Left and Alt+Shift+Right navigate, Alt+Enter accepts, and Escape returns
  from inline editing to the review panel. The PDF action offers review or download when
  unreviewed changes remain. Older versions open with the same editing tools and display
  available source snapshots. Review metadata and controls are excluded from rendered PDFs.
- `ApplicationPreparationPage.tsx` — uses the editor for v2 documents; removes the manual
  ChatGPT handoff (brief copy, paste/import result) and the raw LaTeX textarea.

## Prompts and voice

Short, positive, per-stage prompts (`common.txt`, `cv.txt`, `letter.txt`, `review.txt`,
`answers.txt`). Committed examples are fictional. The profile may hold `voice_notes`
(≤ 1500 chars) and up to 3 `style_exemplars` per language; they are fenced as voice only.
The CV generation schema gives each base bullet a required keep/rewrite slot keyed by
`bullet_id` with a `relevance` level (`high`, `medium`, `low`) and gives each entry an
`order` list. Medium- and low-relevance slots keep the base text verbatim as
`origin="source"`. High-relevance slots supply rewritten text bound to that exact base
source while preserving the original lead-in. A typical CV rewrites
two to four core-duty bullets at the source's exact scope, applying posting vocabulary
where equivalent. Skills can only be reordered. The profile has two required sentence
slots and one nullable third slot, each with a separate evidence list limited to one
entry plus general facts. Entry-scoped facts identify work and study contexts explicitly.
The profile opens with a verified professional identity, connects to the strongest
work evidence, and adds a distinct technical or analytical strength when relevant.
Each sentence has its own evidence, and the sequence avoids restating bullet wording.
The headline uses the posting’s role-family language for verified functional work.
The German profile uses implicit first person
and the English profile uses noun-led or subject-less CV prose.

The letter opens with a cited connection between the applicant's background and a core
duty. Each selected employer or study example synthesizes one or two verified actions
and an outcome or takeaway for a named duty. The best evidence can come from a study
project, with the context made explicit. The letter generation schema gives each employer
or institution one optional employer-scoped example slot plus source-cited `motivation`
and optional `fit` blocks. A selected example names its posting duty, anchors to one entry
and cites an allowed source per sentence.
The closing uses an idiomatic interview invitation in the target language.
Subjects are formatted deterministically from the posting title (`Bewerbung als <Titel>` in
German, `Application for <title>` in English). The tailored CV is available during review,
where textual overlap and claim-scope issues can be surfaced as findings. Wording drift
is advisory and can carry an exact quote plus a proposed replacement. These findings
never initiate a generation retry. In PDF rendering, the heading, entry header, intro
and first project stay together. If a two-page CV underfills the first page, education
can start there with one complete project and continue on page two.

## Work packages

1. WP-A Documents core (done): schema, import_tex, import_docx, project_tex, evidence,
   grounding, editorial, text + unit tests on fictional fixtures.
2. WP-B Frontend view (done): types, CvView, LetterView, CSS, fonts, print entry,
   DocumentEditor, DocumentReview + node tests.
3. WP-C Render + pipeline (done): application_pdf, application_pipeline, prompts, retries,
   private eval harness `scripts/eval/application_documents.py` (reads only `data/private/`).
4. WP-D Storage, queue, API, migration, page integration, handoff removal, legacy cleanup,
   docs (`docs/architecture.md`, `docs/application-preparation.md`, `README.md`) (done).
5. WP-E Acceptance (done): generated on private cases and judged on a 0–10 rubric (job fit,
   ATS parsability, editability, no unverified claims, common CV/letter criteria), iterating
   until every judge scored every document ≥ 8.

## Verification

Focused unit and node tests during development; frontend build; Chromium render tests skip
when no browser is installed. PostgreSQL migration and queue tests run in CI. Real profile
data, outputs and judge transcripts stay in gitignored `data/private/`.

Pipeline version `application-documents-v2.15` resets `common.txt`, `cv.txt`, `letter.txt`,
and `review.txt` to concise, principle-based prompts (removing accumulated case-specific rules
while keeping the keep/rewrite bullet and employer-scoped letter contracts), adds an optional
`prompts_dir` / `RADIUS_PROMPTS_DIR` override for side-by-side prompt evaluation, and replaces
the absolute 0–10 score loop with a private blind pairwise evaluation and hard-check suite
(`scripts/eval/eval_v2.py`).
