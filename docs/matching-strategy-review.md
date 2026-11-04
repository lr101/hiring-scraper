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
then AI requirement assessment for the final score. The existing worker visits
locally plausible jobs at 30 or above. This is a recall-oriented shortlist, not
proof of fit. Local numbers shown in Radius are provisional and capped at 59;
the local/AI source and connection or quota status remain distinguishable.
Saved minimum-score filters above 59 intentionally wait for AI results; these
filters do not prevent the worker from considering the provisional shortlist. Local eligibility
restrictions continue to apply.

The AI must identify the job's main responsibilities, domain expertise and
seniority independently of the applicant's skills. It marks requirements as core
or supporting, quotes job and applicant sources, and distinguishes direct support,
transferable evidence, explicit missing qualifications and unknowns. Goals,
writing examples and desired titles cannot establish existing abilities. Literal
source checks verify provenance, not interpretation or extraction completeness.

The weighted dimension estimate cannot exceed demonstrated role evidence. Core
requirements have an 85-point budget and supporting requirements at most 15;
without supporting requirements the core budget is 100. Within each group,
preferred requirements have quarter weight, direct support earns full credit and
transferable evidence half credit. Unknown and missing evidence add no demonstrated
credit. Any unresolved mandatory core requirement caps the index at 59. Without
identified core work the evidence ceiling is zero. A list of matching generic
tools or favorable preferences cannot lift that ceiling.

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
is claimed by synthetic transport tests.

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
