# Improvements from the real-company form gaps

The same captured German-company DOM and the same independent expectations
were replayed before and after the production changes. No new company pages
were loaded. The baseline remains in `results.json`; the new runtime is
measured in `after-improvements.json`. Capture and expectation hashes match.

| Visible scored outcome | Before | After |
| --- | ---: | ---: |
| Correct fills | 28 | 54 |
| Missed fills | 29 | 5 |
| Wrong format / preservation violations | 3 | 0 |
| Correctly preserved | 101 | 102 |
| Total scored | 161 | 161 |

Experience targets improved from **0/18 to 18/18**. Contact targets improved
from **28/41 to 36/41**. All replays remained stable when repeated and recorded
zero submission attempts, transport attempts or allowed browser requests.
The isolation self-check again verified five denied transport mechanisms,
two prevented direct submissions and an independent browser-route abort.

## What changed

- Required prefixes such as `Pflichtfeld` no longer hide supported labels.
  Closely associated labels with broken `for` IDs and single-question
  paragraphs are recognized. A work-history label in the immediately previous
  paragraph is accepted only when the current paragraph has one control and
  no own text, and the previous paragraph contains no controls. A paragraph never borrows a label explicitly
  attached to another existing control.
- Bare `Name` beside one unambiguous `Vorname` becomes surname. Explicit
  full-name prompts and `autocomplete=name` retain their meaning. A labeled
  leave-empty trap is not counted as evidence of a first-name question.
- Postal code/city and a full postal address can be composed from the saved
  components. Missing components block partial postal-address fills. Separate
  street/house-number questions do not receive a combined address line.
  Related given-name and house-number questions are located across nested
  semantic question wrappers, up to the current form, with foreign-person
  controls excluded.
- Work-history recognition includes h4 headings, `Beruflicher Werdegang` and
  `Tätigkeitsbezeichnung`. The real Ammerländer row's broken label association
  can now supply employer, role and location without inventing exact days.
- Saved jobs can be formatted into a complete history textarea, numbered
  one-line entries, latest-employer fields and paired period / employer-duties
  fields. Current roles use `Present`; stored year/month precision is retained.
  These are deterministic copies of saved facts, not CV parsing or inference.
- Numbered and combined draft collections pause if populated values differ
  from the saved order. Existing content is preserved, and a short field's
  maximum length does not cause silent truncation. Composed history values are
  checked against native input constraints before writing, including numeric
  date-range patterns that cannot accept the saved date precision.

## Remaining limits

Five visible contact targets remain empty: two independent phone/mobile
questions are deliberately ambiguous, two site-specific street abbreviations
are still unrecognized, and an availability question lies inside a broad
career section that remains conservatively scoped. The install does not claim
that every application is complete.

compact's 55 template controls still require framework hydration and remain
unscored. Two other targets are initially hidden. Dynamic Add/Next/Save editors,
external CSS, site scripts and server validation remain outside this offline
DOM experiment. Plain text history uses profile order; it does not parse or
reorder arbitrary existing prose. The captures now inform implementation, so
these results are regression evidence rather than a new held-out estimate.

## Reproduce

```sh
# No company requests: uses local, unchanged captures.
.venv/bin/python experiments/evaluate_real_autofill.py \
  --output /tmp/autofill-real-after.json
RADIUS_EXTENSION_BROWSER_TESTS=1 .venv/bin/python -m unittest \
  discover -s tests -p test_real_autofill_improvements.py -v
```

The new offline regression file has 29 cases covering positive behaviors and
preservation variants. Tests use literal expected outputs and fictional saved
facts. Missing behaviors were observed failing before their production fixes.
The MV3 suite additionally exercises the profile-to-worker-to-injected-runtime
path for full and combined history fields.

See [original baseline and methodology](README.md), [baseline output](results.json)
and [updated output](after-improvements.json) for all source URLs and scored values.

The unchanged 499-form / 950-field synthetic manifest was also replayed with
the final runtime: 521 correct fills and 429 correct preservations, with no
missed/wrong fills, unstable repeats, submissions, page errors or network
requests. Its manifest and current runtime hashes were verified against
`../autofill-lab/expanded-after-real-fixes.json`.

## Final verification — 2026-10-20

All 113 extension integration tests passed in two disjoint module groups
(65 and 48 tests), including both real-extension history-formatting cases.
Those two fixtures now declare UTF-8 explicitly so Chromium receives the
intended German labels. The 29 captured-form regression cases, 33 JavaScript
tests, 10 profile API tests, extension formatting and production frontend build
also passed. Independent review approved the final implementation.

Both offline studies were replayed with the final runtime. Frozen manifest,
capture and expectation hashes were verified; the results above were unchanged.
The referenced result files contain the final runtime hashes. The fresh public
preview's profile editor was checked in Chromium, and its ZIP and checksum
matched the local package byte for byte.
