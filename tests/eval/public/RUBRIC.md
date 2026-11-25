# Labelling rubric (public composite set)

Each case is one fictional candidate profile against one job summary. Labels judge
**how well the job suits the candidate** from the saved profile alone, not how a matcher
scores it.

## Grades

| Grade | Meaning | Typical evidence |
|---|---|---|
| 3 strong | Target role family at the candidate's level. Core duties overlap substantially and stated requirements (tenure, tools, language levels) are met. | Same duties, same level, tools and languages match. |
| 2 good | Clearly in the candidate's field or a saved secondary role, with one material gap that is normal to bridge: a new domain, a missing tool, a step above or below the current level, a qualitative language stretch. | One gap. |
| 1 adjacent | Transferable skills only: neighbouring function or domain, much more senior role, or the duties overlap by a minority. | Several gaps or a different function. |
| 0 irrelevant | A different profession, or the profile's keywords appear only incidentally (tool names, "Scrum", "onboarding", "SAP"). | Keyword traps, unrelated work. |

A qualitative language wish ("fluent", "verhandlungssicher", "sehr gute Deutschkenntnisse")
lowers the content fit by at most one grade and never makes a job ineligible.

## Hide policy (`should_hide`)

A job is hidden **only for an explicit, quotable hard failure**. Everything else lowers
the rank and stays visible.

| Tag | Hide when |
|---|---|
| `hard_hide_cefr` | The posting states a CEFR minimum above the saved level for that language ("Deutsch C1", "English at C1 level is required"), or mandatory native-level German. |
| `hard_hide_excluded_term` | The job title contains a term from the profile's `excluded_terms`. |
| `hard_hide_declined_onsite` | The saved profile has `willing_to_work_onsite: false` and the posting text explicitly requires on-site presence ("five days a week in the office", "remote work is not possible"). |
| `hard_hide_work_permit` | The posting requires an existing work permit or right to work for the named jurisdiction and excludes sponsorship, while the profile says `authorized_to_work: false` there. |

A hidden case is graded on content fit but capped at **grade 1**: an ineligible job is never
"good". `should_hide` with grade >= 2 is invalid.

Never hidden (the matcher must keep these visible), tagged accordingly:

- `qualitative_language`: fluency wording without a CEFR level.
- `seniority_gap`: seniority or leadership above the profile.
- `metadata_only_onsite`: `work_arrangement: onsite` metadata without any attendance text.
- `sponsorship_alternative`: a permit requirement with an offer to sponsor or assist.

## Other adversarial tags

- `keyword_trap`: incidental skill or tool mentions in an unrelated profession.
- `company_boast`: company-history claims ("15 years of experience") that are not requirements.
- `or_alternative`: "5 years, or 3 years with a degree" style alternatives the profile satisfies.
- `long_clause`: a requirement written as one very long sentence.

## Splits

`dev` (about 70%) and `heldout` (about 30%) are assigned by the SHA-256 of the job's group key.
Jobs with near-identical text (5-gram Jaccard >= 0.6) share a group key, so near-duplicates
never straddle splits.

Target acceptance gates run on both `overall` and `heldout`. Because `heldout` has roughly
55–75 visible cases spread across 101 integer scores, `max_value_share` on splits uses the
small-sample bin bound `allowed_max_bin = max(3, ceil(0.05 * n))` (so 3 coincidental integer
ties out of 56 visible cases passes while 4 or 5 fails, and large `n` behaves identically to
the strict 5% cap enforced on `overall`). No other target gate is relaxed on splits.
