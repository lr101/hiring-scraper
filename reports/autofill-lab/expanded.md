# Expanded profile-first autofill evaluation

This records the synthetic study before the real-company fixes. See
[the subsequent improvements](../autofill-real/improvements.md) and
`expanded-after-real-fixes.json` for the later regression run.

The source is the user's saved profile. No CV parsing, external inference
service or new extension permission is involved.

## Larger examples, explicit expectations

The corpus grows from 33 fields / 14 tiny forms to **499 forms and 950 scored
fields**, representing **116 distinct semantic examples** plus layout,
answer-state and record-order variants. Labels use wrapped labels, ARIA text,
`aria-labelledby`, placeholders and narrowly adjacent labels. Cases include
English/German wording, foreign-person and education sections, conflicting
attributes, disabled/readonly/existing controls, native choices, confirmations,
conditional context changes and repeated work records.

The labels and literal expected outcomes live in
[autofill_corpus.py](../../experiments/autofill_corpus.py), separately from
production aliases. Supporting controls keep an otherwise unknown target form
discoverable. They are chosen to avoid duplicating a scored contact fact and
are excluded from the 950 scored fields. Each case repeats the fill and records
unexpected value changes, submissions, JavaScript errors and network requests.

This is a synthetic regression corpus, not a held-out study or a representative
sample of real ATS websites. Five layouts of a prompt are five form variants,
not five independent semantic examples. Known facts and expected assignments
are explicitly specified; they are never calculated by the matcher under test.

## Measured results

The identical frozen manifest produces these outcomes with the saved pre-change
runtime and the final runtime:

| Scored outcome | Before | After |
| --- | ---: | ---: |
| Correctly filled | 218 | 521 |
| Correctly preserved | 356 | 429 |
| Missed fill | 303 | 0 |
| Wrong fill | 73 | 0 |
| Total | 950 | 950 |

Both runs recorded zero changes on repeated fills, unexpected submissions,
page errors or network requests. Runtime hashes in this study report matched the
source evaluated at the time. Two consecutive preparation/fill passes took a median 20.85 ms,
95th percentile 60.16 ms and maximum 161.46 ms in this local corpus run,
excluding browser startup. These timings are diagnostic, not an ATS performance
guarantee. Input/change cache invalidation remains necessary for visibility
changes driven by CSS pseudo-classes without DOM mutation.

## Implemented findings

1. **Match saved records using existing facts.** Prefilled employer/title,
   known start/end year or month, and checked current-role controls constrain
   candidate jobs. A bipartite matching assigns distinct saved jobs to
   anchored rows. Removing each chosen edge and solving again proves the
   assignment unique. If another full assignment exists, preserve the history
   for review. Blank rows receive the remaining saved jobs in profile order.
   This supports reordered jobs and lets a distinctive row disambiguate another
   row that shares its employer. Dates retain their known precision.
2. **Reuse explicit contact confirmations.** One primary email or phone plus
   recognized confirmation fields can reuse the same fact. English/German
   confirmation phrasing is bounded to those contact facts. Two independent
   primary fields remain ambiguous. Consent and declarations are not treated
   as contact confirmations.
3. **Recheck the live question.** Before each field, compare its current
   canonical key and visible label against the discovered question. A changed
   label, section or disconnected control stops later writes. Already accepted
   entries are retained and the report requests a fresh review.
4. **Recognize plain section headings.** Direct headings in ordinary divs now
   participate in reference/emergency/history context, closing false fills
   where a form lacked fieldsets or ARIA grouping.
5. **Broaden bounded English/German wording.** Added unambiguous contact,
   city, postal, weekly-hours, on-site and link wording. Brand normalization
   preserves LinkedIn/GitHub when camelCase splitting is used for identifiers.

Only DOM nodes and numeric profile indices are retained by record bindings.
Profile values are not cached globally. DOM row indices used by captured-form
schemas stay stable when the selected source job differs from visual row order.
Bindings reset for each fill and are rebuilt after adding a row.

## Reproduce

The evaluator runs the actual `history.js` and `best-effort.js` source in
Chromium, including history preparation, native writes and repeated fills.
It does not emulate the scorer or derive expected values from runtime output.
The separate MV3 regression suite covers extension/storage/worker integration.

```sh
.venv/bin/python experiments/autofill_corpus.py
.venv/bin/python experiments/evaluate_autofill.py \
  --output /tmp/autofill-after.json
RADIUS_EXTENSION_BROWSER_TESTS=1 .venv/bin/python -m unittest \
  tests.application_companion.test_best_effort \
  tests.application_companion.test_structured_extension -v
```

`expanded-before.json` and `expanded-after.json` contain every scored output,
runtime SHA-256 hashes, and a hash of the entire fixture manifest (including
profile inputs and expected values). Their manifest hashes must match before
comparing them. `expanded-manifest.sha256` records the frozen manifest hash.
Before was measured against a copy of the pre-change runtime, not reconstructed
from the modified matcher. `--runtime-dir PATH` can evaluate any saved runtime.

## Remaining limits

The corpus checks isolated browser-injected runtime and synthetic form layouts;
it does not prove behavior on every framework widget or employer site. Existing
real React application/editor flows run in the MV3 integration suite. Custom
modals, shadow roots, cross-origin frames, richer education records and explicit
user-taught mappings remain useful next work. No nearest-neighbor/LLM answers
were promoted to automatic fills.

The independent review added extra reversed date-only and current-only
regressions outside the frozen corpus. They reproduced a missed-fill case and
now verify that known dates/current status can identify a saved job without a
prefilled employer. Month-only, location-only and free-text descriptions do not
establish identity on their own; they still constrain compatibility once a
stronger anchor exists.

## Integration verification

After the final runtime changes, the complete MV3 browser suite passed all
111 tests. The 33 JavaScript tests, 10 profile integration tests, seven existing
profile-document tests, extension formatting/syntax checks and frontend
production build passed. The report's family filter was checked at 360 px and
728 px widths without horizontal overflow or browser errors.

[Interactive outcome comparison](expanded-explorer.html)
