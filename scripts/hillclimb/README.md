# Application generation hillclimb

This runner evaluates the server's **full application-preparation worker** with a
real Radius ChatGPT subscription. It builds a temporary SQLite profile, saved CV
and job from a private scenario file, then calls the deployment's
`_prepare_application` worker. That runs the production snapshot, requirement
assessment, drafting, independent review, revision, final grounding review,
document validation and PDF checks. The temporary database is removed after the
run. The final package, progress metadata and diagnostic assessment/draft/review
checkpoints are kept in the private run directory. Checkpoints and error details
can contain applicant information. Treat the whole run directory as personal data.

The runner does not use canned model responses. Each fresh fixture has no cached
matching assessment, so a successful full run uses five Codex requests:
requirement assessment, draft, independent review, revision and final review.
Automatic format correction can add another request. The selected Radius model
setting applies unless the scenario or command overrides it. `fast` mode is
available for debugging, but benchmark comparisons should use the default `full`
mode.

The runner checks subscription usage before building the fixture and forwards the
existing 50% usage limit to every provider call. Unavailable usage or usage at or
above that limit stops the run; it does not change the account's limits.

## Scenario file

Keep the scenario JSON and its `.tex` CV in a private directory outside the
repository. The CV path is relative to the JSON file.

```json
{
  "version": 1,
  "scenario_id": "role-2026-01",
  "cv_file": "base-cv.tex",
  "language": "en",
  "profile": {
    "name": "Applicant",
    "summary": "Verified career summary",
    "skills": ["Project coordination"],
    "application_personalization": {
      "career_evidence": "Evidence-backed profile facts",
      "writing_samples": "Optional style samples",
      "cover_letter_example": "Optional letter example",
      "wording_to_avoid": "",
      "career_goals": "",
      "work_preferences": ""
    }
  },
  "job": {
    "title": "Project Manager",
    "company_name": "Example GmbH",
    "description": "The exact saved job description",
    "url": "https://employer.example/jobs/123",
    "location_text": "Berlin",
    "work_arrangement": "hybrid",
    "employment_type": "full_time"
  },
  "application_notes": "Optional per-application instructions"
}
```

`profile` accepts the normal Radius profile fields. The `.tex` CV must be a CV
file accepted by the current Radius upload path. Job fields accepted by the
runner are `title`, `company_name`, `description`, `url`, `location_text`,
`salary`, `work_arrangement`, `employment_type`, `schedule`, `is_remote`,
`seniority`, `department` and `date_posted`.

## Run candidates concurrently

Run one command per candidate/worktree. Give each run a unique candidate label
and run ID. They share the configured Radius Codex state and its file lock, so
model calls queue safely while each worker uses its own temporary database and
output folder.

```sh
python3 scripts/hillclimb/run_application_generation.py \
  --scenario /private/benchmark/role-2026-01.json \
  --candidate prompt-variant-a \
  --run-id trial-001
```

The process uses `RADIUS_CODEX_STATE_DIR` when set, otherwise the Radius
default `~/.local/state/radius/codex`. That directory must already be connected
to the ChatGPT subscription used by the deployment. The runner never reads
`~/.codex` credentials or asks for credentials. The deployment's Codex runtime
serializes provider calls, even when several candidate processes run at once.

Artifacts default to
`~/.local/state/radius/application-hillclimb/<scenario>/<candidate>/<run-id>/`.
Set `RADIUS_HILLCLIMB_RESULTS` or pass `--output-root` to change the destination.
Each result directory is private (`0700`); files are `0600`. A run ID is
single-use so one worker cannot overwrite another worker's output. The
`application.json` contains the generated CV, cover letter and review material;
handle it as personal data. `run.json` records the code fingerprint, model,
stages and status. There is no automatic expiry: retain a run while comparing or
auditing it, then delete its entire directory when it is no longer needed,
including `checkpoints/`. Deleting the temporary SQLite database does not remove
these retained diagnostic copies.

Candidate scorecards are intentionally kept outside the generation request:
compare factual grounding against the supplied CV/profile, relevance to the
same saved posting, CV structure, letter specificity/voice, and the generated
PDF checks. The workflow's review notes are useful evidence but are not a
substitute for human scoring. Keep scenario, model, mode and job fixed across
candidate runs.

## Compare independently assigned scores

The runner records private `progress.json` and `checkpoints/` artifacts while it
works, plus scenario/base-CV hashes and sanitized API error details in `run.json`.
Checkpoints are diagnostic artifacts; each new run starts fresh.

After an evaluator assigns scores, compare them with the frozen historical
baseline:

```sh
python3 scripts/hillclimb/compare_scores.py \
  --baseline /private/benchmark/baseline/scores.json \
  --candidate /private/benchmark/candidate/scores.json \
  --provenance /private/benchmark/candidate/provenance.json \
  --output /private/benchmark/candidate/score-comparison.json
```

Both score files use a `documents` array with `case_id`, `document_kind` (`cv` or
`cover_letter`), `job_fit_score` and `checklist_score` (integers 1–10). For each
historical document, both scores must reach the baseline independently. Each
represented role also needs scores for both newly generated documents, including
companions with no historical score. Duplicate scores and invalid values are
rejected, and existing comparison files are never overwritten.

The required version-1 provenance manifest binds `baseline_sha256` and
`candidate_scorecard_sha256` to their files. Its `suite` reference (`path`,
`sha256`) points to an independently archived suite containing the baseline
digest, `mode: "full"`, `requested_model` (null for the Radius default), and a
`cases` object mapping case IDs to `scenario_sha256` and `base_cv_sha256`.
Archive the suite before generation and keep it fixed across candidates.

Manifest `runs` contains exactly one record per suite case: `case_id`, `scenario`,
`base_cv`, `run`, and `package` file references, each with `path` and `sha256`,
plus `scored_package_sha256` recorded by the evaluator from what it actually
scored. Paths are relative to the manifest, or absolute. The manifest's
`evaluator` records `model: "gpt-6.1-sol"` and `reasoning_effort: "high"`.
The checker requires the same candidate code fingerprint across runs and verifies
their completed status, `requested_model`, mode, sources and output hashes. The
runner records the resolved runtime setting separately as `model`. An
unset requested model checks the default setting; it does not prove which model
the account default resolved to. Preserve runtime evidence for that separately.
Hash checks bind evidence to files; the evaluator's judgment and the archived
suite remain trusted inputs. Do not reconstruct the evaluator's package digest
from a different or subsequently modified output.

Exit status 0 means score coverage and thresholds pass. Confirm successful
production generation, factual grounding, unchanged sources and usable rendered
artifacts separately before declaring a candidate complete. Job-fit scores based
only on a title remain provisional when the exact posting is unavailable.
