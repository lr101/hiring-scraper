# Matching strategy: job requirements before incidental skills

## Finding in the existing implementation

`matching.py::_content_overlap` divides overlapping recognized capabilities by the
recognized job capabilities, and can award up to 85 points once two substantive
capabilities match. `match_job` also starts desired-title matches at 55 and takes
the maximum of this path and content overlap. Incidental/optional labels and a
small general-skills exclusion set already help, but cannot account for core
requirements that the bounded bilingual vocabulary never recognized. Recognizing
two secondary tools while missing the actual discipline can therefore create a
misleadingly high percentage. Adding every skill in the applicant CV does not
solve the missing job-side requirements.

## Decision

Use local rules and semantic similarity for inexpensive candidate preselection,
then AI requirement assessment for selected jobs. The worker uses the bounded
priority/exploration pool described below; it does not eventually assess every
unassessed job. Exploration can include unfamiliar wording within that pool.
Explicit known
incompatibilities, closed/expired jobs, source checks and subscription limits
remain enforced. This is a recall-oriented shortlist, not
proof of fit. Local numbers shown in Radius retain their evidence-based calibration and remain provisional;
the local/AI source and connection or quota status remain distinguishable.
Saved minimum-score filters can admit grounded local estimates without waiting for AI.
These filters do not prevent the worker from considering the provisional shortlist. Local eligibility
restrictions continue to apply.

The AI must identify the job's main responsibilities, domain expertise and
seniority independently of the applicant's skills. It marks requirements as core
or supporting, quotes job and applicant sources, and distinguishes direct support,
transferable evidence, explicit missing qualifications and unknowns. Goals,
writing examples and desired titles cannot establish existing abilities. Literal
source checks verify provenance, not interpretation or extraction completeness.

The skills/experience qualification estimate cannot exceed demonstrated role
evidence. Career/working-style preferences are scored separately. Core
requirements have an 85-point budget and supporting requirements at most 15;
without supporting requirements the core budget is 100. Within each group,
preferred requirements have quarter weight, direct support earns full credit and
close transferable evidence 80% credit and adjacent transfer half credit. Unknown and missing evidence add no demonstrated
credit. Unresolved core tasks reduce aggregate evidence rather than imposing a blanket 59 cap.
They still prevent a Recommended label. Without
identified core work the evidence ceiling is zero. A list of matching generic
tools or favorable preferences cannot lift that ceiling.

Requirement category is separate from mandatory/preferred priority. Formal
education, credentials, languages, location, work authorization and workplace
conditions constrain eligibility rather than task evidence arithmetic. Unknown
mandatory formal or legal prerequisites trigger explicit eligibility review; they
do not establish an applicant incompatibility. A verified independent conflict
cannot be cleared by supporting another prerequisite.

Structured feed leaves retain their own source paths and required/preferred
scope. HTML heading levels end sibling sections without erasing child scope.
Both local and AI matching receive the same explicit CV work/study projection;
contacts and CV headers remain excluded. Provider citation options retain parent
scope and explicit degree completion state. One transient transport timeout may
retry through the normal lock/quota guard, separately from one grounding repair.

`task_alignment.py` adds a source-backed action/deliverable diagnostic alongside
legacy local scoring. It compares actual work text rather than titles, preserves
academic scope and distinguishes assistance from ownership. Coordinated duties
are evaluated separately, so support for one duty cannot stand in for another.
Multiple grounded duties can raise the local estimate. Partial supported duties
or multiple transferable duties can surface a possible lead, capped at 59.
The separate local `task_fit` field records capability overlap before mandatory
qualification penalties and preferences; the final `score` remains the constrained
shortlist estimate. Required degree evidence contributes to the eligibility gate,
including completed degrees parsed from the saved CV. Current study does not
establish degree completion. A past end date does not prove graduation: CV-derived
degree proof requires an affirmative completion or award statement and must not
contradict withdrawal or noncompletion evidence. Profile actions are split at the
same predicate boundaries as job duties, so ownership of one object cannot prove
ownership of an assisted duty elsewhere in that sentence. This diagnostic still needs broad
calibration; neither literal action overlap nor embedding similarity proves
professional tenure, formal prerequisites or regulated competence.

These are explicit conservative starting rules, not empirically calibrated hiring
probabilities. Evidence completeness is shown separately so absence of evidence is
not mistaken for proven inability. Unknowns are excluded from the separate
known-evidence coverage diagnostic, but remain in the demonstrated-evidence ceiling.

## Why not rely exclusively on a stronger static score?

A static scorer could improve with employer-requirement extraction, task/domain
classification, role-specific importance and verified experience levels. Those
are useful directions, but implementing them as a growing alias vocabulary repeats
the current failure for unseen professions and paraphrases. Once validated
requirements and their importance exist, deterministic arithmetic is appropriate;
this change keeps that arithmetic in code and uses AI for contextual interpretation.

AI also has failure modes: misclassifying a supporting skill as core, omitting a
requirement, misreading a source quote, or overvaluing an adjacent project. A
successful quote check cannot rule these out. No live-model quality improvement
is claimed by synthetic transport tests. Provider citations select bound source/
excerpt references; the application resolves and validates them before caching.
An invalid semantic status or gate can receive one guarded reconciliation pass.
Unscoped authorization flags are excluded from matching evidence; unknown legal
eligibility cannot be inferred from a generic form answer.

## Remaining evaluation

Before tuning the budgets or claiming one method is more accurate, label 20–30
representative strong, adjacent and unrelated jobs against the same saved career
facts. Include traps with shared reporting/tools but unrelated core work, matching
titles but wrong domain/seniority, adjacent duties with unfamiliar titles, sparse
postings and explicit qualification gaps. Compare the original static ranking,
a static requirement-based alternative and AI requirement assessment on false
strong matches, top-result relevance, missed suitable jobs, evidence completeness,
latency and subscription use. Inspect jobs below the local shortlist threshold too;
AI reranking cannot recover suitable jobs excluded by preselection.

The current regression tests establish deterministic safeguards: many supporting
matches cannot compensate for unknown core work, required gaps stay visible,
stale assessments fall back to provisional estimates, and sparse postings can be
processed without fabricating a requirement. They do not replace the judged-job
benchmark.


## Selective AI policy and combining scores

Static matching and cached local embeddings process the inventory without paid
inference. The automatic AI pool is at most 10% of locally eligible jobs, rounded
up, capped at ten per profile and two per employer. About 80% are the highest
static candidates; the balance samples task/static disagreement and uncertainty.
Unknown eligibility is distinct from a known blocker. This is a relevance and
value-of-information heuristic, still requiring independent quality evaluation.

Compute pool membership from static evidence including jobs already assessed by
AI. A rejected assessment must not free a slot for another job automatically;
otherwise a small batch size eventually becomes an all-inventory AI sweep.
Profile/job facts and inventory changes can legitimately change the pool. The
persisted global rolling-five-hour provider-call budget defaults to 50, and counts
repairs and unsuccessful attempts as well as successful assessments. There is no
50% subscription usage cutoff. Connection, quota availability and provider
exhaustion checks still apply. This controls automatic matching calls, not unrelated CV generation
or other activity on the account.
Actual quota exhaustion is rechecked under the runtime lock before every provider
attempt, including repairs and timeout retries. Usage below 100% remains eligible;
unavailable or exhausted quota does not start inference. A missing qualification
score remains unknown when an academic-scope ceiling is applied.

For unselected jobs retain the explicitly provisional static estimate and its
uncertainty. For selected jobs use a current grounded AI requirement judgment;
never average scores with different meanings or let favorable AI preferences
clear known static incompatibilities. Separate task qualification, mandatory
eligibility and uncertainty in the result. Cached judgments invalidate on relevant
source changes; only jobs still in the bounded pool are automatically refreshed.

## Diagnosing a stalled assessment queue

The profile matching worker gives local index pages, dirty jobs, semantic
embeddings, AI assessments, and automatic preparation a turn each cycle.
A pending or failed index page must not skip later stages: AI selection reads
current source facts directly and validates them again before saving an answer.
Shortlist preparation also runs before quota checks so a connection pause does
not prevent the worker from persisting its selection.

When Settings shows queued pairs but no successful assessment:

- Confirm background workers are enabled (`RUN_BACKGROUND_WORKERS=true`) and
  look for `profile match worker started` in the app logs. The status endpoint's
  `worker_health: unknown` is not a heartbeat or proof the process is alive.
- Check `indexing_pending` and `selection_pending`. A cold selection count is an
  estimate before shortlist caps; it does not promise that every pair will receive
  an assessment. Selection expires after a minute, so a stale selection alone
  does not prove the worker has stopped.
- Check subscription availability and the separate five-hour call budget. Worker
  logs report `AI matching paused` with a safe reason. A zero subscription usage
  percentage alone does not establish that inference is allowed.
- Look for `Profile assessment failed` entries. They identify the candidate and
  exception type without logging prompts, applicant facts, or provider responses.
  Failed candidates use bounded exponential backoff, allowing other candidates
  to proceed. Repeated errors need investigation of the runtime or validation
  path; do not bypass eligibility, quota, or source validation to drain a queue.

Deploy worker changes with an application restart. Verify that a permitted
candidate produces a saved assessment and advances `last_assessed_at` on the
actual deployment; passing local tests cannot confirm production runtime access.
