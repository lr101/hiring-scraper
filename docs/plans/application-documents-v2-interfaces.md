# Application documents v2: module interfaces

Companion to `application-documents-v2.md`. These signatures are the contract between
the pipeline/render package (WP-C) and storage/queue/API (WP-D).

## Render (`hiring_scraper/app/application_pdf.py`)

```python
@dataclass
class RenderResult:
    pdf: bytes
    pages: int
    text: str                    # pypdf-extracted text
    checks: list[dict]           # [{code, status: 'passed'|'warning'|'failed', detail}]
    page_limit_exceeded: bool    # cv > 2 pages, letter > 1 page

def available() -> bool          # Chromium + built frontend/dist/print.html present
def render_document(kind: Literal['cv', 'letter'], document: dict, *, photo_png: bytes | None = None,
                    timeout: float = 30) -> RenderResult
```
- Loads `RADIUS_FRONTEND_DIST` (default `frontend/dist`) `print.html` via `file://`, injects
  `window.__RADIUS_DOCUMENT__ = {kind, document, photo}` with `add_init_script`, aborts every
  request that is not a file under the dist folder, waits for `body[data-ready="1"]`, then
  `page.pdf(prefer_css_page_size=True, print_background=True)`.
- One lazily started browser per process, a semaphore of 2 renders, restart on crash.
- Raises `RenderUnavailable` (no browser/dist) or `RenderFailed(code)`; never logs document text.

## Generation (`hiring_scraper/app/application_pipeline.py`)

```python
Generate = Callable[[str, dict], dict]   # (prompt, json_schema) -> parsed JSON; raises
                                         # GenerationError(code, retryable: bool)

@dataclass
class StageHooks:
    load: Callable[[str], dict | None]            # checkpoint lookup by key
    save: Callable[[str, dict], None]             # checkpoint store (also renews the lease)
    publish: Callable[[str, dict, list[dict]], None]  # (kind 'cv'|'letter', body, findings)
    stage: Callable[[str], None]                  # progress label
    cancelled: Callable[[], bool]

@dataclass
class PreparationInput:
    base_cv: dict            # CvDocument JSON (language of the application)
    profile: dict            # saved preferences dict (facts + application_personalization)
    job: dict                # {title, company_name, location, url, description}
    language: Literal['en', 'de']
    mode: Literal['full', 'fast'] = 'full'
    today: date

@dataclass
class PreparationResult:
    cv: dict; letter: dict; findings: list[dict]; telemetry: dict; stages: dict

def input_digest(inp: PreparationInput) -> str   # fact-bearing inputs only
def prepare_documents(inp, generate: Generate, hooks: StageHooks,
                      render: Callable[[str, dict], RenderResult] | None = None) -> PreparationResult
def revise_unit(document: dict, kind: str, unit_id: str, instruction: str, inp: PreparationInput,
                generate: Generate) -> dict   # returns {unit_id, text, sources, findings}
def codex_generate(*, model: str | None, cancelled: Callable[[], bool] | None) -> Generate
```
- Stages `cv` → `letter` → `review` (full only). Each stage's result is checkpointed under
  `f'{stage}:{digest}'`; a resumed run skips stages whose checkpoint exists.
- Each model call: up to 3 attempts with jittered backoff (≈5 s, 20 s) when
  `GenerationError.retryable` or the JSON fails schema/semantic validation (the next prompt
  includes the fixed validation message). Non-retryable errors propagate.
- `publish` is called after the CV stage, after the letter stage and after review.
- Findings come from `hiring_scraper.documents.grounding` plus review findings plus render
  checks. Nothing is discarded because of a finding.

## Storage / API (`hiring_scraper/app/application_documents_v2.py`)

Table `application_documents(profile_id, job_id, kind, version, language, body JSONB,
findings JSONB, origin, stale_inputs, input_digest, created_at)`, PK
`(profile_id, job_id, kind, version)`; keep the newest 20 versions per (profile, job, kind).
Table `application_document_renders(profile_id, job_id, kind, version, template, pdf BYTEA,
pages, checks JSONB, created_at)` caches verified PDFs.

Routes under `/api/v1/profiles/{profile_id}`:
- `POST /application-documents/prepare` `{job_id, language, mode}` → 202 `{task}`
- `GET /application-documents/task?job_id=` → `{task}` with `{id, kind, state, stage,
  attempts, error_code, error, next_attempt_at, stages}` and task-specific metadata.
  A completed revision includes the proposed text and findings in `result` for explicit acceptance.
  A generated document held after a concurrent save is available in `held`.
- `POST /application-documents/task/cancel?job_id=&task_id=`
- `GET /application-documents?job_id=` → `{cv: Doc|null, letter: Doc|null}`;
  `Doc = {version, language, body, findings, origin, stale_inputs, created_at}`
- `PUT /application-documents/{kind}?job_id=` `{expected_version, body}` → `{version}`; 409 on conflict.
  Optional `dismissed_findings` records review dismissals and `held_task_id` adopts a held document.
- `POST /application-documents/{kind}/revert?job_id=` `{version, expected_version?}` → `{version}`
- `POST /application-documents/{kind}/revise?job_id=` `{unit_id, instruction, expected_version?}` → 202 `{task}`
- `GET /application-documents/{kind}/pdf?job_id=&version=` → 200 PDF when cached, else
  202 `{state: 'rendering'}` and a render task is queued (worker lane)
- `GET /cv/structure?language=` → `{document, source: 'stored'|'imported', errors}`;
  `PUT /cv/structure?language=` `{document}` stores `variants[lang].document` and the projected
  marked `text` through the existing CV save path (declares the source change).
