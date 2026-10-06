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
