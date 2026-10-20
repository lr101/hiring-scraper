# Initial profile-first autofill experiments

**The larger follow-up is in [499-form / 950-field evaluation](expanded.md).**
This page and its 33-field results record the initial 2026-10-19 investigation.
The follow-up implements unique record assignment, contact confirmation reuse
and stronger live section checks; use its results for the latest comparison.

This investigation treats the user's saved profile as the source of truth. A
CV is not required. No CV parsing or model request was added to the extension.

## Changes applied to the application

- Radius profiles now have reusable work-history records: company, position,
  location, responsibilities, known start/end year or month, and current-role
  status. Records have stable IDs and sync through the existing companion
  connection. The standalone extension already had a work-history editor.
- Work-history discovery now recognizes complete semantic cards made with
  fieldsets, ARIA groups, sections and ordinary divs beneath those sections.
  The smallest complete group represents a record; an enclosing section is
  not counted as another job. Within a recognized work record, generic Name
  and Position can mean company and job title, with a date/current-role field
  required before treating generic Name as an employer. Multiple identity
  fields prevent a list wrapper from being treated as one record.
- Applicant contact facts no longer fall back into reference, emergency,
  education or unrecognized work-history sections. An outer heading such as
  Employment application or Education application still permits applicant
  contacts. Existing structured work-history conflict checks remain in place.
- Discovery starts from identity fields and caches only DOM groups. DOM,
  input/change and viewport changes invalidate the cache, and a fill starts
  with fresh discovery. A local timing trial with two contact fields and
  1,000 decorative divs dropped from about 4,585 ms to 15 ms after narrowing
  and caching. This is a single synthetic timing trial, not an ATS benchmark.
  Input/change invalidation is intentionally retained because CSS visibility
  can change from `:checked` or `:placeholder-shown` without DOM mutations.
  A larger trial filled 52 fields across 10 records plus 1,000 decorative
  divs in about 2,010 ms; per-field rediscovery remains an optimization target.
- A captured-question startup race found during verification is fixed: the
  preparation page rereads stored questions after registering its listeners,
  covering captures that arrived between initial render and subscription.

The broader scoring and assignment algorithms below remain experiments. They
are not imported by production code. Custom modals, shadow roots, cross-origin
frames and education-history autofill still need additional work.

## Research

Primary sources inspected on 2026-10-19:

- [FormFilla architecture](https://github.com/AhmadHassan-BTed/FormFilla):
  documents normalized field signals, repeated-block detection, weighted
  bipartite assignment and native DOM setters/events. Its core engine is
  closed source. This is an independently implemented experiment, not an
  inspection or reproduction of its private implementation.
- [Bitwarden fill scripts](https://contributing.bitwarden.com/architecture/deep-dives/autofill/generating-fill-scripts/):
  describes matching multiple field attributes before generating fill actions.
- [Simplify autofill](https://help.simplify.jobs/help/articles/2415391-using-copilot-to-autofill-applications):
  describes reusable profile information, distinct common/unique questions,
  and reusing saved answers when an exact question appears again.

## Reproduce

Use the repository's existing locked environment, Chromium and dependencies:

```sh
.venv/bin/python experiments/autofill_lab.py
.venv/bin/python experiments/autofill_lab.py --embeddings
```

The optional command uses the project's pinned local multilingual E5 encoder
(`intfloat/multilingual-e5-small@ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3`).
Its first run downloads the model to the configured model/cache directory.
It runs on CPU, sends no profile data to an inference service, and does not
change extension dependencies. Model initialization and batched encoding
timing is included in the results, not browser or per-field latency.

`results.json` contains the current source hash and every prediction.
The [interactive explorer](explorer.html) presents the recorded results and
lets you inspect wrong matches and missed facts for each technique.
`baseline-before-context-fix.json` preserves observations before the new
section guard; rerunning the harness evaluates current source, not that old
snapshot. The extension is exercised in Chromium with real DOM writes.
Prototype matchers only produce mappings; their results do not validate
framework event handling or site acceptance.

## Results and limits

There are **33 hand-authored fields across 14 small fictional forms**: 22 have
a known target fact and 11 should be left unanswered. This is an exploratory
stress set, not an ATS accuracy estimate, held-out evaluation or comparison
of commercial products. Labels and aliases were authored together; this
strongly favors the rules prototype. Thresholds are exploratory and embedding
similarities are not probabilities.

| Technique | Correct fills | Wrong fills | Correctly skipped | Missed known facts |
| --- | ---: | ---: | ---: | ---: |
| Extension before section fix | 12 | 6 | 7 | 8 |
| Current extension | 12 | 0 | 11 | 10 |
| Fuzzy labels alone | 13 | 8 | 6 | 6 |
| Section constraints + ambiguity margin | 19 | 0 | 11 | 3 |
| Gated one-to-one assignment | 17 | 0 | 11 | 5 |
| Local E5 nearest match | 19 | 14 | 0 | 0 |
| E5 + conservative section/margin gates | 3 | 0 | 11 | 19 |

Prototype matchers receive a typed fact catalog including employment and
education facts. The extension receives contacts and supported structured work
history, but has no corresponding education adapter. Counts therefore measure
the entire constructed task, not an isolated algorithm comparison on identical
production capabilities. The deliberately fragmented work-card cases in this
set do not contain a complete company/title block; the separate browser
regression test covers real complete cards and repeat filling.

The six observed wrong fills before the fix included the applicant's name in
school/employer Name fields, plus their name/email in reference and emergency
cards. The extension now skips those uncertain mappings. The production card
test separately demonstrates filling complete work groups from saved facts.

One-to-one assignment missed two legitimate repeated facts: Email/Confirm
email and Company/Confirm company name. The toy solver applies per-field
confidence gates before exact assignment and is limited to these tiny forms;
it is not a production Hungarian implementation. A production engine needs
record-scoped capacities and explicit confirmation-field reuse, rather than
one unique use of each profile fact across the entire page.

The embedding result argues against simply picking the nearest profile field:
it also picks something for sensitive and unrelated questions. Conservative
gates remove those errors here but leave most known fields unresolved. A
useful semantic fallback needs separate calibration and review, not a claim
that these scores prove reliability.

## Next implementation direction

1. **Typed records set up once.** Extend the same profile/editor/sync approach
   to schools, qualifications and explicit language proficiency. Keep them
   separate from job-search preferences and free-form notes.
2. **Score within the right record.** Combine visible labels, ARIA,
   autocomplete and identifier hints; use the enclosing section to determine
   whose information a field asks for. Preserve contradictory-signal checks.
3. **Solve assignment per entity.** Match a work card to one saved job and a
   school card to one saved education record. Use existing populated fields as
   identity anchors. Permit confirmation fields to reuse a fact; abstain when
   record identity is ambiguous. Never resolve ties solely by eliminating
   another candidate.
4. **Learn explicit corrections.** Offer an action such as “This field means
   my current city” and store the chosen canonical key, question text, control
   type, section and site scope. Keep employer-specific and personal answers
   scoped separately. Do not automatically learn every value typed on a page.
5. **Preview the fill plan.** Show proposed value, profile record, signal and
   ambiguity before running unfamiliar mappings. Verify retained values after
   writing and rediscover the form after choices reveal new fields.
6. **Try semantic suggestions last.** Use local embeddings or an optional
   model to propose a typed field key or existing fact ID, rather than invent
   an answer. Reject nonexistent facts and present uncertain suggestions for
   review. Vision can be a future label/layout fallback for custom widgets;
   it was not evaluated here.

Before promoting the prototype, add an independent fixture corpus with real
React-controlled forms, reordered/partially populated records, multiple
identical employers, conditional questions, custom comboboxes and unfamiliar
English/German wording. Optimize correct retained values and false fills,
not just the number of inputs written.

## Verification

- Extension formatting and all 33 JavaScript tests pass.
- All 17 API/profile and attachment tests pass with `PYTHONPATH=tests`.
- TypeScript/Vite production build passes.
- The 102-case browser run passed 101 cases and exposed a captured-question
  subscription race. A deterministic regression failed before the fix and
  passed after it. All 13 production-flow tests then passed together,
  including the new regression and saving/syncing work history without a CV.
  The other 90 browser cases passed in the full run.
- The interactive explorer's technique selection, filtering and responsive
  layout were checked in Chromium at 728 px and 360 px widths. T3's inline
  headless preview could not launch on this host.
