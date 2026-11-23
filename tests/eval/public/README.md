# Public composite evaluation set

A committed, offline evaluation set for the Radius job matcher. Everything here is **fictional**.

## Contents

- `profiles/*.json`: six composite candidate profiles (product owner in fintech, project
  coordinator in manufacturing, learning & development specialist, junior prototyping engineer
  who needs sponsorship, operations and process-improvement analyst, logistics team lead).
  They mix German and English profile text and CEFR levels from A2 to C2. They are archetypes
  invented for this repository: they are not derived from any real person's CV, and they use
  no names, employers, contact details or links.
- `jobs/job_NNNN.json`: original job summaries written for this set in both languages, in the
  style of public postings ("Ihre Aufgaben" / "Ihr Profil", "What you'll do" / "What you bring").
  Employers are generic descriptors such as "mid-size fintech". No text is copied from a real
  posting; ids are neutral.
- Translation pairs: 17 jobs carry a `pair_id` (`pair_NN`) and exist as a faithful DE and EN
  version (the translated job also lists `translation_of`). Requirement wording strength is
  preserved (CEFR levels stay explicit, "verhandlungssicher" becomes "fluent, negotiation-ready").
  Both members get the same grade, `should_hide` and tags per profile and always share a split
  (the split hashes the `pair_id`). The runner compares scores and eligibility per
  `(profile_id, pair_id)` to report DE/EN parity.
- `labels.json`: graded labels per case (`grade` 0-3, `should_hide`, tags, rationale, split).
- `RUBRIC.md`: grade definitions and the hide policy.

## Use

```bash
python -m tests.eval.run                 # public set (default)
python -m tests.eval.run --dataset private
python -m tests.eval.public.check        # validate the dataset
python -m unittest tests.test_eval_regression
```

`tests/eval/baseline.json` holds the baselines of this public set only. The private real-CV
set stays outside the repository (`data/eval/cv`, gitignored, or `RADIUS_PRIVATE_EVAL_DIR`)
with its own baseline, and the regression tests skip its gates when it is absent.

## Target acceptance gates

Set `RADIUS_EVAL_TARGET_MATCHERS=v2` when running `tests.test_eval_regression` to check the
absolute acceptance gates on both `overall` and `heldout`. `overall` enforces the strict
`max_value_share <= 0.05` cap on rounded integer scores; splits (including `heldout`) use
the small-sample bin bound `max(3, ceil(0.05 * n)) / n` over the split's `n` visible cases
so 3–4 coincidental integer collisions on ~55–75 visible jobs do not fail as a false plateau,
while genuine bunching (e.g. 5/56 = 8.93%) still fails and large splits converge to 5%. All
other target gates (`strong_reach_80`, `irrelevant_below_30`, `spread_p5_p95`,
`lang_visa_mean_abs_delta`, `lang_visa_band_jumps`, `hide_precision`, `hide_recall`,
`good_hidden_rate`) apply identically to `overall` and `heldout`.

## Sources and independence

Job facts were written from general knowledge of how such postings read, then varied to hit
specific matcher behaviours (keyword traps, company boasts, OR alternatives, qualitative
language wording, seniority gaps, metadata-only on-site flags, sponsorship alternatives,
long clauses, and hard hide conditions). The check refuses any six-word run shared with the
private profiles, any public profile that shares more than three calendar years with a private
one, and any eight-word run shared with a private job, whenever the private set is present.
