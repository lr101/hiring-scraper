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

Rank jobs locally only, using deterministic rules (`matching.py`, `requirements.py`,
`duty_evidence.py`, `task_alignment.py`) combined with local multilingual semantic
evidence (`semantic_matching.py`, cached in `match_semantic_documents`). AI job-fit
assessment for ranking has been removed (its tables are dropped by migration
`0043_drop_ai_assessment`, which also invalidates legacy derived match state for a
clean local rebuild while preserving user-set Journey stages). AI requirement
grounding remains strictly inside the application drafting workflow.

Structured feed leaves retain their own source paths and required/preferred scope.
HTML heading levels end sibling sections without erasing child scope. Local matching
receives the explicit CV work/study projection while keeping contacts and CV headers
excluded. Unscoped authorization flags are excluded from matching evidence; unknown
legal eligibility is never inferred from a generic form answer.

Static matching uses documented duty coverage when a posting has several identifiable
responsibilities and the profile provides distinct work examples. The scorer checks
the performed action, deliverable, specialist context, and responsibility before
awarding direct or transferable credit. Sparse postings and profiles with limited
work evidence retain provisional discovery signals. The job detail shows the duty
excerpts and supporting profile evidence.

Requirement category stays separate from mandatory/preferred priority. Formal
education, credentials, languages, location, work authorization, and workplace
conditions constrain eligibility rather than task evidence arithmetic. Unknown
mandatory formal or legal prerequisites stay visible as uncertain rather than
silently passing or proving applicant incompatibility.

Local multilingual section embeddings supplement exact vocabulary matching so
unfamiliar wording and paraphrases still surface as explained leads without sending
profile or job data to a remote model or coupling ranking to subscription quotas.

## Why local-only ranking?

Removing the AI ranking queue keeps job matching deterministic, fast, private, and
independent of ChatGPT subscription limits. An earlier AI ranking queue carried
inherent failure modes — misclassifying supporting skills as core, omitting
requirements, misreading source quotes, and coupling Journey freshness to external
quotas — while duplicating work better handled locally. Deep requirement grounding
and independent document review remain part of **Prepare inside Radius** when
drafting an actual application.

## Remaining evaluation

Evaluate strong, adjacent, and unrelated jobs against frozen fit judgments and
a saved profile snapshot. Include shared tools in unrelated roles, matching
titles with different scope or seniority, transferable duties with unfamiliar
titles, sparse postings, and explicit qualification gaps. Report false strong
matches, missed suitable jobs, and results for duties-based and discovery-based
ranking so that a sample dominated by low-fit jobs does not conceal recall gaps.

The current regression tests establish deterministic safeguards: many supporting
matches cannot compensate for unknown core work, required gaps stay visible,
stale rows refresh through the local worker without blocking reads, and sparse
postings can be processed without fabricating requirements. They do not replace
the judged-job benchmark.

## Local refresh pipeline

The profile match worker (`app.profile_match_worker`) backfills stale profiles in
bounded, restartable pages (`profile_match_progress`), refreshes local semantic
section vectors (`match_semantic_documents`), and drains changed jobs from
`match_dirty_jobs`. Each stage runs independently so a transient failure in one
stage does not block the others, and HTTP reads never run model inference inline.
