# Task 2 report: JavaScript career extraction PoC

Implemented a static, no-JavaScript extractor for the observed server-rendered
`data-guide-id="joblist-card"` contract. It preserves query-string detail URLs,
uses explicit card locations and tags, and suppresses duplicate heading hints.

Saved-capture replay command:

```bash
.venv/bin/python experiments/javascript_career_extraction_poc.py --run-dir data/career-poc/run-31 --sample-size 250
```

Output: 1,728 eligible unique captures; a deterministic 250-capture slice had
7 Next data, 2 Next Flight, 9 Nuxt, 70 endpoint-reference, and 71 job-script
markers. Generic embedded JSON yielded 0 jobs. The first-party Wibu Next Flight
capture `cf39ff8b1927aa89fb50` changed from 2 synthetic headings to 10 trusted
explicit cards; title/destination deduplication yields 8 incremental postings.
No endpoint requests or browser rendering ran.

Focused tests (red before implementation, then green):

```bash
.venv/bin/python -m unittest tests.test_html_jobs.HtmlJobExtractionTests.test_extracts_explicit_server_rendered_job_cards_with_query_detail_urls tests.test_html_jobs.HtmlJobExtractionTests.test_explicit_job_card_keeps_an_active_initiative_posting
```

Output: `Ran 2 tests in 0.011s` / `OK`.

Focused module command and output:

```bash
.venv/bin/python -m unittest tests.test_html_jobs
```

Output: `Ran 20 tests in 0.040s` / `OK`.

Full suite command and output:

```bash
.venv/bin/python -m unittest discover -s tests
```

Output: `Ran 146 tests in 1.460s` / `OK`.

## Review fix round 1

Tightened structured static-card extraction to reject hidden, disabled, closed,
template, cross-origin, credential, and non-HTTP destinations. An initiative
application card is now only an unconfirmed role candidate. Structured cards
use normalized query destinations as their primary identity; duplicate
destinations merge while same-title different IDs remain separate. Generic
framework JSON arrays are unconfirmed same-origin role candidates at most;
closed/template rows are rejected and only JSON-LD `JobPosting` remains active.

Replay command and output artifact:

```bash
.venv/bin/python experiments/javascript_career_extraction_poc.py --run-dir data/career-poc/run-31 --sample-size 250 --output data/career-poc/js-extraction-poc-2026-10/replay.json
```

Output: 1,775 metadata-selected eligible captures; exactly 250 bodies loaded;
9 new active first-party Wibu destination identities, 2 synthetic-heading title
overlaps/replacements, 7 roles without a prior title/location hint, and 1
unconfirmed initiative candidate.
Artifact: `data/career-poc/js-extraction-poc-2026-10/replay.json`.

Focused module command and output:

```bash
.venv/bin/python -m unittest tests.test_html_jobs
```

Output: `Ran 24 tests in 0.045s` / `OK`.

Full suite command and output:

```bash
.venv/bin/python -m unittest discover -s tests
```

Output: `Ran 150 tests in 1.409s` / `OK`.

Final verification after separating stable identity and title-overlap metrics:

```bash
.venv/bin/python -m unittest tests.test_html_jobs && .venv/bin/python -m unittest discover -s tests
```

Output: `Ran 24 tests in 0.045s` / `OK`; then `Ran 150 tests in 1.393s` / `OK`.

## Talent-pool hardening

Structured-card labels that are clearly generic talent pools or communities are
now unconfirmed role candidates. Specific vacancies such as `Talent Acquisition
Manager` remain active structured jobs.

Focused module command and output:

```bash
.venv/bin/python -m unittest tests.test_html_jobs
```

Output: `Ran 25 tests in 0.046s` / `OK`.

Full suite command and output:

```bash
.venv/bin/python -m unittest discover -s tests
```

Output: `Ran 151 tests in 1.440s` / `OK`.

## Review fix round 2

Structured-card labels `Initiative Application`, `Unsolicited Application`,
and `General Application` now join the existing German initiative and
talent-pool/community paths as unconfirmed role candidates. The pattern is
anchored, so `General Application Engineer` remains an active vacancy.
Inactive card detection now recognizes closed/archived class-state tokens and
true `data-template`/`data-disabled` values. Explicit false values leave a
live card eligible.

The new regression tests were first run before the parser change and failed as
expected: the three English generic applications and four inactive cards were
returned as active jobs. After the parser change, the focused regression
command passed:

```bash
.venv/bin/python -m unittest tests.test_html_jobs.HtmlJobExtractionTests.test_structured_generic_application_labels_are_unconfirmed tests.test_html_jobs.HtmlJobExtractionTests.test_structured_cards_reject_inactive_class_tokens_and_true_data_flags -v
```

Output: `Ran 2 tests in 0.011s` / `OK`.

Focused module command and output:

```bash
.venv/bin/python -m unittest tests.test_html_jobs -v
```

Output: `Ran 27 tests in 0.046s` / `OK`.

Full suite command and output:

```bash
.venv/bin/python -m unittest discover -s tests
```

Output: `Ran 153 tests in 1.709s` / `OK`.

## Committed replay reproducibility repair

The experiment no longer imports the uncommitted discovery-time
`trusted_html_jobs` helper. It reads each selected page's saved
`html_extraction_trust` and admits explicit card rows only for `first_party`
and `branded_external` relationships; unverified saved sources add no active
rows. This is intentionally limited to the replay and does not duplicate the
discovery trust policy.

The replay still requires the local `data/career-poc/run-31` capture directory,
whose large response bodies are not committed. The compact,
`data/career-poc/js-extraction-poc-2026-10/replay.json` artifact is committed
for review. The regenerated replay preserves 1,775 eligible captures, the
250-body sample, nine new stable destination identities, two title overlaps,
and seven title-novel rows. The seven-row metric compares normalized titles to
the baseline headings only; it is not location-aware.

The trust-metadata regression was written before the replay change and failed
because the admission step did not exist:

```bash
.venv/bin/python -m unittest tests.test_javascript_career_extraction_poc
```

Output: `Ran 1 test in 0.000s` / `FAILED (failures=1)`.

After adding the saved-trust allow-list, focused verification was:

```bash
.venv/bin/python -m unittest tests.test_javascript_career_extraction_poc tests.test_html_jobs -v
```

Output: `Ran 28 tests in 0.043s` / `OK`.

The regenerated replay command was:

```bash
.venv/bin/python experiments/javascript_career_extraction_poc.py --run-dir data/career-poc/run-31 --sample-size 250 --output data/career-poc/js-extraction-poc-2026-10/replay.json
```

Output: 1,775 eligible captures; 250 selected bodies; 9 structured active
destination identities; 2 baseline-title overlaps; and 7 title-novel rows.

Full-suite verification was:

```bash
.venv/bin/python -m unittest discover -s tests
```

Output: `Ran 154 tests in 1.411s` / `OK`.
