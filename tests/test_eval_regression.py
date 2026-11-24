"""Dataset sanity checks, pure metric unit tests, and regression gates for job matching."""
from __future__ import annotations

import json
import math
import os
import unittest
from collections import Counter, defaultdict
from collections.abc import Mapping

from tests.eval import cv as private_dataset
from tests.eval import public as public_dataset
from tests.eval.ablation import ablate_lang_visa, is_lang_visa_sentence, strip_lang_visa_text
from tests.eval.cv.check import group_near_duplicate_jobs
from tests.eval.cv.check import validate_dataset as validate_private_dataset
from tests.eval.public.check import validate_dataset as validate_public_dataset
from tests.eval.metrics import (
    band_error,
    de_en_parity,
    effective_ranking_score,
    expected_calibration_error,
    good_hidden_rate,
    hide_precision,
    hide_recall,
    lang_visa_ablation_metrics,
    max_value_share_limit,
    ndcg_at_k,
    pair_eligibility_disagreement,
    pairwise_auc,
    percentile,
    precision_at_k,
    rank_indices,
    ranking_auc,
    ranking_relevance,
    score_band,
    score_range_metrics,
    score_spread,
)
from tests.eval.run import (
    BASELINE_PATH,
    HARD_HIDE_TAGS,
    MATCHERS,
    MATCHER_VERSIONS,
    assign_split,
    baseline_path,
    evaluate,
    evaluate_clause_removal,
    get_dataset,
    get_matcher,
    load_cases,
    load_profiles,
    register_matcher,
)

MIN_HARD_HIDE_GATE_CASES = 3
MIN_PROFILE_GATE_CASES = 15
# The public set is a compact 71-case selection (every grade per profile, every hard-hide
# category and adversarial tag, complete DE/EN pairs) so model outputs re-record in ~25 min.
# Translation-pair coverage the dataset must keep, (profile, pair) combinations that are compared.
MIN_PAIR_COUNT = {"public": 9, "private": 0}

# Dataset-specific shape expectations: (min cases, min profiles, must contain profile id)
DATASET_SHAPE = {
    "public": {"min_cases": 70, "min_profiles": 6, "min_cases_per_profile": 10, "required_profile": None},
    "private": {"min_cases": 200, "min_profiles": 5, "min_cases_per_profile": 15, "required_profile": "base"},
}


def _require_dataset(testcase: unittest.TestCase, dataset: str) -> None:
    if not DATASET_AVAILABILITY[dataset]():
        testcase.skipTest(
            f"{dataset} evaluation dataset not present on this machine "
            f"(private data lives in {private_dataset.CV_EVAL_DIR}); gates skipped"
        )


DATASET_AVAILABILITY = {
    "public": public_dataset.is_available,
    "private": private_dataset.is_available,
}

REQUIRED_PROFILE_KEYS = {
    "desired_roles",
    "secondary_roles",
    "skills",
    "languages",
    "language_levels",
    "experience_years",
    "summary",
    "application_details",
    "cv_career_facts",
    "skill_evidence",
    "application_preferences",
    "education",
}


def check_regression_gates(report: dict, baseline: dict, *, min_pairs: int = 0) -> list[str]:
    """Compare an evaluation report against a baseline and return gate failure messages.

    ``min_pairs`` makes the translation-pair gates mandatory: both the baseline and the
    report must then carry at least that many DE/EN pairs with parity metrics.
    """
    failures: list[str] = []
    b_overall = baseline["overall"]
    c_overall = report["overall"]
    if min_pairs:
        for name, overall in (("baseline", b_overall), ("report", c_overall)):
            if int(overall.get("pair_count", 0)) < min_pairs:
                failures.append(f"{name} has {overall.get('pair_count', 0)} DE/EN pairs (< {min_pairs})")
            for metric in ("de_en_parity", "pair_eligibility_disagreement"):
                if overall.get(metric) is None:
                    failures.append(f"{name} lacks {metric}")

    if c_overall["ndcg_at_10"] < b_overall["ndcg_at_10"] - 0.02:
        failures.append(
            f"nDCG@10 regressed: {c_overall['ndcg_at_10']:.4f} < {b_overall['ndcg_at_10']:.4f} - 0.02"
        )
    if c_overall["pairwise_auc"] < b_overall["pairwise_auc"] - 0.02:
        failures.append(
            f"pairwise_auc regressed: {c_overall['pairwise_auc']:.4f} < {b_overall['pairwise_auc']:.4f} - 0.02"
        )
    if c_overall["hide_precision"] < b_overall["hide_precision"] - 0.02:
        failures.append(
            f"hide_precision regressed: {c_overall['hide_precision']:.4f} < {b_overall['hide_precision']:.4f} - 0.02"
        )
    if c_overall["hide_recall"] < b_overall["hide_recall"] - 0.02:
        failures.append(
            f"hide_recall regressed: {c_overall['hide_recall']:.4f} < {b_overall['hide_recall']:.4f} - 0.02"
        )

    b_hard_hide = baseline.get("hard_hide_recall") or {}
    c_hard_hide = report.get("hard_hide_recall") or {}
    b_by_tag = baseline.get("by_tag") or {}
    c_by_tag = report.get("by_tag") or {}
    for tag in HARD_HIDE_TAGS:
        b_tag_entry = b_hard_hide.get(tag) or b_by_tag.get(tag)
        c_tag_entry = c_hard_hide.get(tag) or c_by_tag.get(tag)
        if b_tag_entry is None and c_tag_entry is None:
            continue
        b_count = int((b_tag_entry or {}).get("count", 0))
        if b_count < MIN_HARD_HIDE_GATE_CASES:
            continue
        if c_tag_entry is None:
            failures.append(f"Missing hard-hide category {tag!r} in report")
            continue
        b_rec = b_tag_entry["hide_recall"]
        c_rec = c_tag_entry["hide_recall"]
        if c_rec < b_rec - 0.02:
            failures.append(
                f"{tag} hide_recall regressed: {c_rec:.4f} < {b_rec:.4f} - 0.02"
            )

    if c_overall["good_hidden_rate"] > b_overall["good_hidden_rate"] + 0.02:
        failures.append(
            f"good_hidden_rate regressed: {c_overall['good_hidden_rate']:.4f} > {b_overall['good_hidden_rate']:.4f} + 0.02"
        )
    if c_overall["band_error"] > b_overall["band_error"] + 0.02:
        failures.append(
            f"band_error regressed: {c_overall['band_error']:.4f} > {b_overall['band_error']:.4f} + 0.02"
        )

    # Translation-pair gates apply only when pairs exist
    has_pairs = (
        int(b_overall.get("pair_count", 0)) > 0
        and int(c_overall.get("pair_count", 0)) > 0
        and b_overall.get("de_en_parity") is not None
        and c_overall.get("de_en_parity") is not None
    )
    if has_pairs:
        if c_overall["de_en_parity"] > b_overall["de_en_parity"] + 2.0:
            failures.append(
                f"de_en_parity regressed: {c_overall['de_en_parity']:.2f} > {b_overall['de_en_parity']:.2f} + 2.0"
            )
        if (
            b_overall.get("pair_eligibility_disagreement") is not None
            and c_overall.get("pair_eligibility_disagreement") is not None
            and c_overall["pair_eligibility_disagreement"] > b_overall["pair_eligibility_disagreement"] + 0.02
        ):
            failures.append(
                "pair_eligibility_disagreement regressed: "
                f"{c_overall['pair_eligibility_disagreement']:.4f} > "
                f"{b_overall['pair_eligibility_disagreement']:.4f} + 0.02"
            )

    # Per-language AUC gap growth gate (DE vs EN)
    b_lang_gap = b_overall.get("lang_auc_gap")
    c_lang_gap = c_overall.get("lang_auc_gap")
    if b_lang_gap is not None and c_lang_gap is not None:
        if c_lang_gap > b_lang_gap + 0.05:
            failures.append(
                f"lang_auc_gap regressed: {c_lang_gap:.4f} > {b_lang_gap:.4f} + 0.05"
            )

    b_profiles = baseline.get("per_profile") or {}
    c_profiles = report.get("per_profile") or {}
    for profile_id, b_prof in sorted(b_profiles.items()):
        if int(b_prof.get("count", 0)) < MIN_PROFILE_GATE_CASES:
            continue
        c_prof = c_profiles.get(profile_id)
        if c_prof is None:
            failures.append(f"Missing profile {profile_id!r} in report")
            continue
        if c_prof["ndcg_at_10"] < b_prof["ndcg_at_10"] - 0.05:
            failures.append(
                f"per-profile nDCG@10 regressed for {profile_id}: "
                f"{c_prof['ndcg_at_10']:.4f} < {b_prof['ndcg_at_10']:.4f} - 0.05"
            )
        if c_prof["pairwise_auc"] < b_prof["pairwise_auc"] - 0.05:
            failures.append(
                f"per-profile pairwise_auc regressed for {profile_id}: "
                f"{c_prof['pairwise_auc']:.4f} < {b_prof['pairwise_auc']:.4f} - 0.05"
            )

    return failures


# Absolute acceptance thresholds for upcoming v2 scorer (user requirements 2026-11-23).
TARGET_GATE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "strong_reach_80": (">=", 0.6),
    "irrelevant_below_30": (">=", 0.8),
    "max_value_share": ("<=", 0.05),
    "spread_p5_p95": (">=", 60.0),
    "lang_visa_mean_abs_delta": ("<=", 5.0),
    "lang_visa_band_jumps": ("==", 0.0),
    "hide_precision": (">=", 0.9),
    "hide_recall": (">=", 0.9),
    "good_hidden_rate": ("==", 0.0),
}


def check_target_gates(
    metrics: Mapping[str, object],
    *,
    small_sample: bool = False,
) -> list[str]:
    """Check absolute target acceptance gates against a report or its ``overall`` dict.

    ``overall`` always enforces the strict ``max_value_share <= 0.05`` gate.
    Splits (such as ``heldout``, or any caller passing ``small_sample=True``)
    use the small-sample bin bound ``max(3, ceil(0.05 * n)) / n`` where ``n``
    is ``visible_count`` (falling back to ``count`` when ``visible_count`` is
    omitted, or strict 0.05 if neither is present). Because a 0-100 integer
    scale has 101 bins, 3 coincidental ties on a small heldout split (e.g.
    3/56 = 0.0536) is ordinary sampling noise rather than a score plateau,
    while genuine bunching (e.g. 5/56 = 0.0893) still fails and large splits
    converge to 5% (e.g. 50/1000 = 0.05). No other gate is relaxed.
    """
    overall = metrics.get("overall")
    if isinstance(overall, Mapping):
        failures = [f"overall: {failure}" for failure in check_target_gates(overall, small_sample=False)]
        if "by_split" in metrics or metrics.get("split") == "all":
            by_split = metrics.get("by_split")
            heldout = by_split.get("heldout") if isinstance(by_split, Mapping) else None
            if not isinstance(heldout, Mapping):
                failures.append("Missing heldout metrics")
            else:
                failures.extend(
                    f"heldout: {failure}"
                    for failure in check_target_gates(heldout, small_sample=True)
                )
        return failures

    failures: list[str] = []
    for key, (op, threshold) in TARGET_GATE_THRESHOLDS.items():
        raw = metrics.get(key)
        if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            failures.append(f"Missing target metric {key!r}")
            continue
        val = float(raw)
        if key == "max_value_share" and small_sample:
            n_raw = metrics.get("visible_count")
            if n_raw is None:
                n_raw = metrics.get("count")
            if isinstance(n_raw, int) and not isinstance(n_raw, bool) and n_raw > 0:
                limit = max_value_share_limit(n_raw, base_share=threshold)
                # Reports round shares to 4 decimals (e.g. round(3/56, 4) == 0.0536);
                # accept up to max(limit, round(limit, 4)) so an exact allowed integer
                # bin count never trips on 4-decimal rounding while +1 job still fails.
                if val > max(limit, round(limit, 4)):
                    failures.append(
                        f"{key} above target: {val:.4f} > {limit:.4f} (n={n_raw})"
                    )
                continue
        if op == ">=" and val < threshold:
            failures.append(f"{key} below target: {val:.4f} < {threshold:.4f}")
        elif op == "<=" and val > threshold:
            failures.append(f"{key} above target: {val:.4f} > {threshold:.4f}")
        elif op == "==" and val != threshold:
            failures.append(f"{key} violates target: {val:.4f} != {threshold:.4f}")
    return failures


class TestIndependentClauseRemoval(unittest.TestCase):
    def test_v3_preserves_score_when_independent_language_or_permit_span_is_removed(self) -> None:
        pairs = evaluate_clause_removal("v3")
        self.assertGreaterEqual(len(pairs), 30)
        self.assertEqual({(row["lang"], row["kind"]) for row in pairs}, {
            ("en", "language"), ("en", "permit"), ("de", "language"), ("de", "permit"),
        })
        for row in pairs:
            with self.subTest(pair=row["id"], lang=row["lang"], kind=row["kind"]):
                self.assertTrue(row["visible"])
                self.assertLessEqual(abs(row["original"] - row["removed"]), 5)
                self.assertLessEqual(abs(score_band(row["original"]) - score_band(row["removed"])), 1)


class TestEvalDatasetIntegrity(unittest.TestCase):
    def test_public_dataset_self_check_passes(self) -> None:
        _profiles, _jobs, _labels, errors = validate_public_dataset()
        self.assertEqual(errors, [], "Public dataset validation failed:\n- " + "\n- ".join(errors))

    def test_private_dataset_self_check_passes(self) -> None:
        _require_dataset(self, "private")
        _profiles, _jobs, _labels, errors = validate_private_dataset()
        self.assertEqual(errors, [], "Private dataset validation failed:\n- " + "\n- ".join(errors))

    def test_public_profiles_and_cases_schema_and_balance(self) -> None:
        self._check_profiles_and_cases("public")

    def test_private_profiles_and_cases_schema_and_balance(self) -> None:
        _require_dataset(self, "private")
        self._check_profiles_and_cases("private")

    def _check_profiles_and_cases(self, dataset: str) -> None:
        shape = DATASET_SHAPE[dataset]
        profiles = load_profiles(dataset)
        jobs = get_dataset(dataset).load_jobs()
        group_keys, _near_pairs = group_near_duplicate_jobs(jobs)
        cases = load_cases(dataset)

        self.assertGreaterEqual(len(profiles), shape["min_profiles"], f"Too few {dataset} profiles")
        if shape["required_profile"]:
            self.assertIn(shape["required_profile"], profiles)
        for profile_id, profile in profiles.items():
            missing_keys = REQUIRED_PROFILE_KEYS - set(profile.keys())
            self.assertFalse(missing_keys, f"Profile {profile_id!r} missing keys: {missing_keys}")
            self.assertIsInstance(profile["application_details"].get("experiences"), list)
            self.assertIsInstance(profile["cv_career_facts"].get("experiences"), list)
            self.assertIsInstance(profile["cv_career_facts"].get("education"), list)

        self.assertGreaterEqual(len(cases), shape["min_cases"], f"Expected >= {shape['min_cases']} cases, got {len(cases)}")

        seen_ids: set[str] = set()
        langs: Counter[str] = Counter()
        splits: Counter[str] = Counter()
        profile_splits: dict[str, Counter[str]] = defaultdict(Counter)
        by_profile: dict[str, list[dict]] = defaultdict(list)
        all_tags: Counter[str] = Counter()

        for case in cases:
            self.assertIsInstance(case, dict)
            for req_key in ("id", "profile_id", "job_id", "lang", "split", "job", "label", "tags"):
                self.assertIn(req_key, case, f"Case missing required key {req_key!r}: {case.get('id')}")

            case_id = case["id"]
            self.assertIsInstance(case_id, str)
            self.assertTrue(case_id.strip(), "Case id must be non-empty")
            self.assertNotIn(case_id, seen_ids, f"Duplicate case id: {case_id!r}")
            seen_ids.add(case_id)

            profile_id = case["profile_id"]
            self.assertIn(profile_id, profiles, f"Unknown profile_id {profile_id!r} in case {case_id!r}")
            by_profile[profile_id].append(case)

            lang = case["lang"]
            self.assertIn(lang, {"de", "en"}, f"Invalid lang {lang!r} in case {case_id!r}")
            langs[lang] += 1

            case_split = case["split"]
            self.assertIn(case_split, {"dev", "heldout"}, f"Invalid split {case_split!r} in case {case_id!r}")
            expected_split = assign_split(group_keys[case["job_id"]])
            self.assertEqual(
                case_split,
                expected_split,
                f"Case {case_id!r} has split {case_split!r}, expected deterministic {expected_split!r}",
            )
            splits[case_split] += 1
            profile_splits[profile_id][case_split] += 1

            job = case["job"]
            self.assertIsInstance(job, dict, f"job must be a dict in {case_id!r}")
            self.assertIsInstance(job.get("title"), str, f"job.title must be a str in {case_id!r}")
            self.assertTrue(job["title"].strip(), f"job.title must be non-empty in {case_id!r}")
            self.assertIsInstance(job.get("description"), str, f"job.description must be a str in {case_id!r}")
            self.assertEqual(job.get("description_kind"), "summary")
            self.assertLessEqual(len(job["description"]), 2500)
            if "work_arrangement" in job and job["work_arrangement"] is not None:
                self.assertIn(job["work_arrangement"], {"remote", "hybrid", "onsite"})

            label = case["label"]
            self.assertIsInstance(label, dict, f"label must be a dict in {case_id!r}")
            grade = label.get("grade")
            self.assertIn(grade, {0, 1, 2, 3}, f"Invalid grade {grade!r} in {case_id!r}")
            self.assertNotIsInstance(grade, bool, f"grade must be int, not bool in {case_id!r}")
            should_hide = label.get("should_hide")
            self.assertIsInstance(should_hide, bool, f"should_hide must be bool in {case_id!r}")
            if should_hide:
                self.assertLess(
                    grade,
                    2,
                    f"Hard-incompatible should_hide case {case_id!r} must have grade < 2 (got {grade})",
                )

            tags = case["tags"]
            self.assertIsInstance(tags, list, f"tags must be a list in {case_id!r}")
            self.assertNotIn("applied", tags, f"Deprecated 'applied' tag found in {case_id!r}")
            for tag in tags:
                self.assertIsInstance(tag, str)
                all_tags[tag] += 1
                if tag in HARD_HIDE_TAGS:
                    self.assertTrue(
                        should_hide,
                        f"Case {case_id!r} tagged {tag!r} must have should_hide=True",
                    )

        # Every profile must meet the per-profile minimum, cover grades 0-3, and span both splits
        self.assertEqual(set(by_profile.keys()), set(profiles.keys()))
        for profile_id, p_cases in by_profile.items():
            self.assertGreaterEqual(
                len(p_cases),
                shape["min_cases_per_profile"],
                f"Profile {profile_id!r} has fewer than {shape['min_cases_per_profile']} cases",
            )
            grade_set = {c["label"]["grade"] for c in p_cases}
            self.assertEqual(
                grade_set,
                {0, 1, 2, 3},
                f"Profile {profile_id!r} must contain all grades 0-3, found {grade_set}",
            )
            self.assertGreater(profile_splits[profile_id]["dev"], 0, f"Profile {profile_id!r} missing dev cases")
            self.assertGreater(
                profile_splits[profile_id]["heldout"],
                0,
                f"Profile {profile_id!r} missing heldout cases",
            )

        # Heldout split ~25-35%
        heldout_ratio = splits["heldout"] / len(cases)
        self.assertGreaterEqual(heldout_ratio, 0.20, f"Heldout share too low: {heldout_ratio:.2%}")
        self.assertLessEqual(heldout_ratio, 0.40, f"Heldout share too high: {heldout_ratio:.2%}")

        # Balanced DE/EN coverage
        de_ratio = langs["de"] / len(cases)
        self.assertGreaterEqual(de_ratio, 0.40, f"German share too low: {de_ratio:.2%}")
        self.assertLessEqual(de_ratio, 0.60, f"German share too high: {de_ratio:.2%}")

        # Language hard-hide category present with >= 3 cases
        self.assertGreaterEqual(
            all_tags["hard_hide_cefr"],
            MIN_HARD_HIDE_GATE_CASES,
            "Expected at least 3 hard_hide_cefr cases",
        )


class TestEvalMetricsUnit(unittest.TestCase):
    def test_ranking_and_classification_metrics(self) -> None:
        self.assertAlmostEqual(ndcg_at_k([3, 2, 1, 0], 4), 1.0)
        self.assertLess(ndcg_at_k([0, 1, 2, 3], 4), 0.7)
        self.assertAlmostEqual(precision_at_k([3, 2, 1, 0], 2), 1.0)
        self.assertAlmostEqual(precision_at_k([3, 0, 2, 1], 4), 0.5)

        # Hidden jobs receive zero gain in observed ranking while keeping true grade in ideal ranking
        self.assertAlmostEqual(
            ndcg_at_k([3, 2, 1, 0], 4, hidden_in_ranked_order=[True, True, True, True]),
            0.0,
        )
        self.assertLess(
            ndcg_at_k([3, 2, 1, 0], 4, hidden_in_ranked_order=[False, True, False, False]),
            1.0,
        )
        self.assertAlmostEqual(
            precision_at_k([3, 2, 1, 0], 2, hidden_in_ranked_order=[False, True, False, False]),
            0.5,
        )
        self.assertAlmostEqual(
            precision_at_k([3, 2, 1, 0], 2, hidden_in_ranked_order=[True, True, False, False]),
            0.0,
        )

        self.assertAlmostEqual(hide_precision([True, False, True], [True, True, False]), 0.5)
        self.assertAlmostEqual(hide_recall([True, False, True], [True, True, False]), 0.5)
        self.assertAlmostEqual(good_hidden_rate([3, 2, 1, 0], [True, False, True, True]), 0.5)
        self.assertAlmostEqual(de_en_parity([(80, 70), (50, 50)]), 5.0)
        self.assertAlmostEqual(pair_eligibility_disagreement([(True, True), (True, False)]), 0.5)
        self.assertAlmostEqual(pair_eligibility_disagreement([(True, True), (False, False)]), 0.0)

        # Hidden cases rank last (effective score -1)
        scores = [95, 40, 20]
        hidden = [True, False, False]
        order = rank_indices(scores, hidden, case_ids=["a", "b", "c"])
        self.assertEqual(order, [1, 2, 0])
        eff = [effective_ranking_score(s, h) for s, h in zip(scores, hidden)]
        self.assertEqual(eff, [-1.0, 40.0, 20.0])
        self.assertAlmostEqual(pairwise_auc([2, 1, 0], [80, 50, 20]), 1.0)
        self.assertAlmostEqual(pairwise_auc([2, 1, 0], [80, 50, 50]), 5.0 / 6.0)
        self.assertAlmostEqual(pairwise_auc([2, 1], [50, 50]), 0.5)

        # Band error on visible jobs only
        self.assertAlmostEqual(band_error([0, 1, 2, 3], [0, 30, 79, 100]), 0.0)
        self.assertAlmostEqual(band_error([0, 3], [39, 70]), 0.10)
        # Hidden job is excluded from band_error
        self.assertAlmostEqual(
            band_error([0, 3], [15, 0], is_hidden=[False, True]),
            0.0,
        )

        # Perfect calibration -> ECE == 0.0
        self.assertAlmostEqual(
            expected_calibration_error([0, 1, 2, 3], [15, 45, 70, 90]),
            0.0,
        )
        # Hiding a grade-3 job degrades ECE
        self.assertGreater(
            expected_calibration_error([3], [90], is_hidden=[True]),
            0.5,
        )

        spread = score_spread([29, 59, 85, 42])
        self.assertEqual(spread["distinct_count"], 4)
        self.assertAlmostEqual(spread["plateau_fraction"], 0.75)
        self.assertEqual(spread["plateau_counts"], {"29": 1, "59": 1, "85": 1})

    def test_should_hide_cases_carry_no_ranking_gain(self) -> None:
        self.assertEqual(ranking_relevance([3, 1, 0, 2], [False, True, False, True]), [3, 0, 0, 0])
        with self.assertRaises(ValueError):
            ranking_relevance([1], [])
        # A correctly hidden grade-1 case keeps nDCG at 1.0 (zero gain in both rankings) ...
        relevance = ranking_relevance([3, 2, 1], [False, False, True])
        self.assertAlmostEqual(ndcg_at_k(relevance, 3, hidden_in_ranked_order=[False, False, True]), 1.0)
        # ... while hiding a case that should stay visible still loses gain.
        relevance = ranking_relevance([3, 2, 1], [False, False, False])
        self.assertLess(ndcg_at_k(relevance, 3, hidden_in_ranked_order=[False, False, True]), 1.0)
        # Without the should_hide rule, the same correct hide is punished.
        self.assertLess(ndcg_at_k([3, 2, 1], 3, hidden_in_ranked_order=[False, False, True]), 1.0)

    def test_ranking_auc_excludes_should_hide_cases(self) -> None:
        grades, scores = [2, 1, 0], [80.0, 50.0, -1.0]
        # The hidden grade-0 case would score -1 (below everything); the hidden grade-1 case below grade 0.
        self.assertAlmostEqual(ranking_auc(grades, scores, [False, False, False]), 1.0)
        self.assertAlmostEqual(ranking_auc([2, 1, 0], [80.0, -1.0, 20.0], [False, True, False]), 1.0)
        self.assertAlmostEqual(pairwise_auc([2, 1, 0], [80.0, -1.0, 20.0]), 2.0 / 3.0)
        self.assertAlmostEqual(ranking_auc([1, 0], [-1.0, 20.0], [True, False]), 0.5)
        with self.assertRaises(ValueError):
            ranking_auc([1], [1.0], [])

    def test_pairs_are_keyed_by_profile_and_pair_id(self) -> None:
        from tests.eval.run import _pair_eligibilities, _pair_scores
        rows = [
            {"profile_id": "a", "pair_id": "pair_01", "lang": "de", "score": 80, "eligible": True},
            {"profile_id": "a", "pair_id": "pair_01", "lang": "en", "score": 60, "eligible": True},
            {"profile_id": "b", "pair_id": "pair_01", "lang": "de", "score": 10, "eligible": False},
            {"profile_id": "b", "pair_id": "pair_01", "lang": "en", "score": 10, "eligible": True},
            {"profile_id": "c", "pair_id": "pair_01", "lang": "en", "score": 40, "eligible": True},
            {"profile_id": "a", "pair_id": None, "lang": "de", "score": 1, "eligible": True},
        ]
        self.assertEqual(_pair_scores(rows), [(80.0, 60.0), (10.0, 10.0)])
        self.assertEqual(_pair_eligibilities(rows), [(True, True), (False, True)])

    def test_gate_thresholds_for_language_gap_sparse_categories_and_small_profiles(self) -> None:
        base_rep = {
            "overall": {
                "pair_count": 0,
                "ndcg_at_10": 0.70,
                "pairwise_auc": 0.70,
                "hide_precision": 0.90,
                "hide_recall": 0.80,
                "good_hidden_rate": 0.05,
                "band_error": 0.15,
                "de_en_parity": None,
                "pair_eligibility_disagreement": None,
                "lang_auc_gap": 0.04,
            },
            "hard_hide_recall": {
                "hard_hide_cefr": {"count": 10, "hide_recall": 0.80},
                "hard_hide_work_permit": {"count": 2, "hide_recall": 1.0},
            },
            "per_profile": {
                "base": {"count": 150, "ndcg_at_10": 0.70, "pairwise_auc": 0.70},
                "tiny": {"count": 10, "ndcg_at_10": 0.90, "pairwise_auc": 0.90},
            },
        }
        cur_rep = json.loads(json.dumps(base_rep))
        # Regress tiny profile (< 15 cases) and sparse hard-hide (< 3 cases): should NOT fail
        cur_rep["hard_hide_recall"]["hard_hide_work_permit"]["hide_recall"] = 0.0
        cur_rep["per_profile"]["tiny"]["ndcg_at_10"] = 0.10
        cur_rep["per_profile"]["tiny"]["pairwise_auc"] = 0.10
        self.assertEqual(check_regression_gates(cur_rep, base_rep), [])

        # Grow lang_auc_gap by > 0.05: MUST fail
        cur_rep["overall"]["lang_auc_gap"] = 0.10
        failures = check_regression_gates(cur_rep, base_rep)
        self.assertTrue(any("lang_auc_gap" in f for f in failures), failures)

    def test_matcher_output_validation(self) -> None:
        bad_outputs = [
            "not-a-dict",
            {"eligible": True},
            {"score": 50},
            {"score": float("nan"), "eligible": True},
            {"score": float("inf"), "eligible": True},
            {"score": -1, "eligible": True},
            {"score": 101, "eligible": True},
            {"score": True, "eligible": True},
            {"score": "50", "eligible": True},
            {"score": 50, "eligible": 1},
            {"score": 50, "eligible": 0},
            {"score": 50, "eligible": None},
            {"score": 50, "eligible": "true"},
        ]
        for idx, payload in enumerate(bad_outputs):
            name = f"_test_invalid_matcher_{idx}"
            register_matcher(name, lambda _j, _p, out=payload: out)
            try:
                with self.assertRaises(ValueError, msg=f"Expected ValueError for output {payload!r}"):
                    evaluate(name, split="heldout")
            finally:
                MATCHERS.pop(name, None)
                MATCHER_VERSIONS.pop(name, None)

    def test_v3_scores_from_recorded_model_outputs(self) -> None:
        self.assertIn("v3", MATCHERS)
        # Every case, not a sample: recorded outputs are used only when their versions and
        # input hashes match the current posting, CV and models.
        for case in load_cases(split="all"):
            res = get_matcher("v3")(case["job"], case["profile"])
            self.assertIsInstance(res["eligible"], bool)
            self.assertTrue(0.0 <= float(res["score"]) <= 100.0)
            self.assertFalse(res.get("pending"), f"{case['id']} needs current recorded model outputs")

    def test_split_evaluation_partitions_dataset(self) -> None:
        name = "_test_split_dummy"
        register_matcher(name, lambda _j, _p: {"score": 50.0, "eligible": True})
        try:
            all_rep = evaluate(name, split="all")
            dev_rep = evaluate(name, split="dev")
            held_rep = evaluate(name, split="heldout")
            self.assertEqual(
                dev_rep["counts"]["total"] + held_rep["counts"]["total"],
                all_rep["counts"]["total"],
            )
            self.assertEqual(set(all_rep["by_split"].keys()), {"dev", "heldout"})
            self.assertEqual(all_rep["by_split"]["dev"]["count"], dev_rep["counts"]["total"])
            self.assertEqual(all_rep["by_split"]["heldout"]["count"], held_rep["counts"]["total"])
            # A constant matcher scores both members of every translation pair alike.
            self.assertGreaterEqual(all_rep["overall"]["pair_count"], MIN_PAIR_COUNT["public"])
            self.assertEqual(all_rep["overall"]["de_en_parity"], 0.0)
            self.assertEqual(all_rep["overall"]["pair_eligibility_disagreement"], 0.0)
            self.assertIn("de", all_rep["by_language"])
            self.assertIn("en", all_rep["by_language"])
        finally:
            MATCHERS.pop(name, None)
            MATCHER_VERSIONS.pop(name, None)

    def test_score_range_metrics_and_percentiles(self) -> None:
        self.assertAlmostEqual(percentile([], 50), 0.0)
        self.assertAlmostEqual(percentile([42.0], 95), 42.0)
        self.assertAlmostEqual(percentile(list(range(101)), 5), 5.0)
        self.assertAlmostEqual(percentile(list(range(101)), 95), 95.0)
        with self.assertRaises(ValueError):
            percentile([1.0, 2.0], 101.0)

        self.assertEqual([score_band(0), score_band(29), score_band(30), score_band(59), score_band(60), score_band(79), score_band(80), score_band(100)], [0, 0, 1, 1, 2, 2, 3, 3])

        # Visible-only range metrics: hidden grade-3 score=0 and hidden grade-0 score=99 are excluded.
        grades = [3, 3, 3, 2, 2, 0, 0, 0]
        scores = [95.0, 80.0, 0.0, 79.0, 55.0, 10.0, 30.0, 99.0]
        hidden = [False, False, True, False, False, False, False, True]
        rm = score_range_metrics(grades, scores, is_hidden=hidden)
        self.assertEqual(rm["visible_count"], 6)
        self.assertAlmostEqual(rm["strong_reach_80"], 1.0)
        self.assertAlmostEqual(rm["good_in_band"], 0.5)
        self.assertAlmostEqual(rm["irrelevant_below_30"], 0.5)
        self.assertAlmostEqual(rm["max_value_share"], 1.0 / 6.0)
        self.assertAlmostEqual(rm["max_score"], 95.0)
        expected_spread = percentile([10.0, 30.0, 55.0, 79.0, 80.0, 95.0], 95.0) - percentile(
            [10.0, 30.0, 55.0, 79.0, 80.0, 95.0], 5.0
        )
        self.assertAlmostEqual(rm["spread_p5_p95"], expected_spread)

        # Bunching at one rounded score value shows up in max_value_share.
        bunched = score_range_metrics([1, 1, 1, 1], [59.0, 59.0, 59.0, 20.0])
        self.assertEqual(bunched["visible_count"], 4)
        self.assertAlmostEqual(bunched["max_value_share"], 0.75)

        # All hidden -> zeros
        empty = score_range_metrics([3, 0], [90.0, 10.0], is_hidden=[True, True])
        self.assertEqual(empty, {
            "visible_count": 0,
            "strong_reach_80": 0.0,
            "good_in_band": 0.0,
            "irrelevant_below_30": 0.0,
            "max_value_share": 0.0,
            "spread_p5_p95": 0.0,
            "max_score": 0.0,
        })

    def test_lang_visa_ablation_metrics(self) -> None:
        empty = lang_visa_ablation_metrics([])
        self.assertEqual(empty["lang_visa_ablated_count"], 0)
        self.assertEqual(empty["lang_visa_mean_abs_delta"], 0.0)
        self.assertEqual(empty["lang_visa_max_delta"], 0.0)
        self.assertEqual(empty["lang_visa_band_jumps"], 0.0)

        # (78, 81): band 2 -> band 3 (1 band crossing, NOT a >1 band jump)
        # (55, 85): band 1 -> band 3 (2 band crossings -> jump!)
        # (60, 62): band 2 -> band 2 (0 crossings)
        res = lang_visa_ablation_metrics([(78.0, 81.0), (55.0, 85.0), (60.0, 62.0)])
        self.assertEqual(res["lang_visa_ablated_count"], 3)
        self.assertAlmostEqual(res["lang_visa_mean_abs_delta"], (3.0 + 30.0 + 2.0) / 3.0)
        self.assertAlmostEqual(res["lang_visa_max_delta"], 30.0)
        self.assertAlmostEqual(res["lang_visa_band_jumps"], 1.0 / 3.0)

    def test_check_target_gates(self) -> None:
        passing = {
            "strong_reach_80": 0.6,
            "irrelevant_below_30": 0.8,
            "max_value_share": 0.05,
            "spread_p5_p95": 60.0,
            "lang_visa_mean_abs_delta": 5.0,
            "lang_visa_band_jumps": 0.0,
            "hide_precision": 0.9,
            "hide_recall": 0.9,
            "good_hidden_rate": 0.0,
        }
        # Accepts both flat metrics dict and full report dict with 'overall'.
        self.assertEqual(check_target_gates(passing), [])
        self.assertEqual(check_target_gates({"overall": passing}), [])
        pooled = {"overall": passing, "by_split": {
            "dev": passing, "heldout": {**passing, "max_value_share": 0.06},
        }}
        self.assertEqual(len(check_target_gates(pooled)), 1)
        self.assertIn("heldout: max_value_share", check_target_gates(pooled)[0])
        self.assertIn("Missing heldout metrics", check_target_gates({"overall": passing, "by_split": {}}))

        # Each threshold fails when violated.
        violations = {
            "strong_reach_80": 0.59,
            "irrelevant_below_30": 0.79,
            "max_value_share": 0.051,
            "spread_p5_p95": 59.9,
            "lang_visa_mean_abs_delta": 5.1,
            "lang_visa_band_jumps": 0.01,
            "hide_precision": 0.89,
            "hide_recall": 0.89,
            "good_hidden_rate": 0.01,
        }
        for key, bad_val in violations.items():
            mutated = {**passing, key: bad_val}
            failures = check_target_gates(mutated)
            self.assertEqual(len(failures), 1, f"Expected 1 failure for {key}={bad_val}, got {failures}")
            self.assertIn(key, failures[0])

        # Missing metric fails cleanly.
        missing = dict(passing)
        missing.pop("spread_p5_p95")
        failures = check_target_gates(missing)
        self.assertTrue(any("spread_p5_p95" in f for f in failures), failures)

    def test_small_sample_max_value_share_bound(self) -> None:
        # Pure limit helper: allowed max bin = max(3, ceil(0.05 * n)).
        self.assertAlmostEqual(max_value_share_limit(56), 3 / 56)
        self.assertAlmostEqual(max_value_share_limit(60), 3 / 60)  # exact 0.05 (not 4/60)
        self.assertAlmostEqual(max_value_share_limit(74), 4 / 74)
        self.assertAlmostEqual(max_value_share_limit(1000), 50 / 1000)
        self.assertAlmostEqual(max_value_share_limit(0), 0.05)

        passing = {
            "strong_reach_80": 0.6,
            "irrelevant_below_30": 0.8,
            "max_value_share": 0.05,
            "spread_p5_p95": 60.0,
            "lang_visa_mean_abs_delta": 5.0,
            "lang_visa_band_jumps": 0.0,
            "hide_precision": 0.9,
            "hide_recall": 0.9,
            "good_hidden_rate": 0.0,
        }

        # n=56 with 3 equal scores (exact and 4-decimal rounded 0.0536) passes on heldout/split...
        for share_3_of_56 in (3 / 56, round(3 / 56, 4)):
            split_ok = {**passing, "visible_count": 56, "max_value_share": share_3_of_56}
            self.assertEqual(check_target_gates(split_ok, small_sample=True), [])
            report_ok = {"overall": passing, "by_split": {"heldout": split_ok}}
            self.assertEqual(check_target_gates(report_ok), [])

        # ...but strict <= 0.05 still applies on overall even if visible_count=56.
        overall_strict = {**passing, "visible_count": 56, "max_value_share": 3 / 56}
        overall_failures = check_target_gates({"overall": overall_strict})
        self.assertEqual(len(overall_failures), 1)
        self.assertIn("overall: max_value_share", overall_failures[0])

        # n=56 with 4 or 5 equal scores fails on heldout/split.
        for bad_count in (4, 5):
            split_bad = {**passing, "visible_count": 56, "max_value_share": round(bad_count / 56, 4)}
            failures = check_target_gates({"overall": passing, "by_split": {"heldout": split_bad}})
            self.assertEqual(len(failures), 1, f"Expected failure for {bad_count}/56, got {failures}")
            self.assertIn("heldout: max_value_share", failures[0])

        # n=74 allows ceil(0.05 * 74) = 4 equal scores (0.0541), rejects 5 (0.0676).
        split_74_ok = {**passing, "visible_count": 74, "max_value_share": round(4 / 74, 4)}
        self.assertEqual(check_target_gates(split_74_ok, small_sample=True), [])
        split_74_bad = {**passing, "visible_count": 74, "max_value_share": round(5 / 74, 4)}
        self.assertEqual(len(check_target_gates(split_74_bad, small_sample=True)), 1)

        # n=1000 behaves like strict 5%: 50/1000 passes, 51/1000 fails.
        split_1000_ok = {**passing, "visible_count": 1000, "max_value_share": 50 / 1000}
        self.assertEqual(check_target_gates(split_1000_ok, small_sample=True), [])
        split_1000_bad = {**passing, "visible_count": 1000, "max_value_share": 51 / 1000}
        failures_1000 = check_target_gates(split_1000_bad, small_sample=True)
        self.assertEqual(len(failures_1000), 1)
        self.assertIn("max_value_share", failures_1000[0])

        # Other gates (e.g. strong_reach_80) are never relaxed on small splits.
        split_low_strong = {**passing, "visible_count": 56, "max_value_share": 3 / 56, "strong_reach_80": 0.5625}
        low_strong_failures = check_target_gates(split_low_strong, small_sample=True)
        self.assertEqual(len(low_strong_failures), 1)
        self.assertIn("strong_reach_80", low_strong_failures[0])


class TestLangVisaAblation(unittest.TestCase):
    def test_removes_en_language_and_visa_sentences(self) -> None:
        desc = (
            "Lead backend services for our payments platform.\n\n"
            "What you bring\n"
            "- 5+ years of Python and PostgreSQL experience\n"
            "- Fluent German (C1) and very good English required\n"
            "- Existing right to work in Germany is required; we cannot offer visa sponsorship\n"
            "- Experience building REST APIs and OAuth authorization services\n"
            "- Good English; German at B1 is helpful\n"
            "- Strong knowledge of German labour law and payroll regulations\n"
            "- Valid work permit for the EU is mandatory\n"
        )
        cleaned, removed = strip_lang_visa_text(desc)
        self.assertEqual(len(removed), 6, removed)
        self.assertIn("Lead backend services for our payments platform.", cleaned)
        self.assertIn("5+ years of Python and PostgreSQL experience", cleaned)
        self.assertIn("Experience building REST APIs and OAuth authorization services", cleaned)
        self.assertIn("Strong knowledge of German labour law and payroll regulations", cleaned)
        self.assertNotIn("Fluent German", cleaned)
        self.assertNotIn("visa sponsorship", cleaned)
        self.assertNotIn("work permit", cleaned)
        self.assertNotIn("German at B1", cleaned)

    def test_removes_de_language_and_visa_sentences(self) -> None:
        desc = (
            "Sie verantworten die Weiterentwicklung unserer Produktionsprozesse.\n\n"
            "Ihr Profil\n"
            "- Abgeschlossenes Studium im Wirtschaftsingenieurwesen oder vergleichbar\n"
            "- Sehr gute Deutschkenntnisse (mindestens C1) sowie gute Englischkenntnisse\n"
            "- Verhandlungssicheres Deutsch und fließendes Englisch in Wort und Schrift\n"
            "- Eine gültige Arbeitserlaubnis oder ein unbefristeter Aufenthaltstitel für Deutschland ist Voraussetzung\n"
            "- Arbeitsbewilligung für die Schweiz erforderlich; kein Visa-Sponsoring möglich\n"
            "- Fundierte Kenntnisse im deutschen Arbeitsrecht und in SAP MM\n"
            "- Gute Sprachkenntnisse in Französisch sind von Vorteil\n"
            "- Führerschein Klasse C1 und Staplerschein\n"
        )
        cleaned, removed = strip_lang_visa_text(desc)
        self.assertEqual(len(removed), 6, removed)
        self.assertIn("Weiterentwicklung unserer Produktionsprozesse", cleaned)
        self.assertIn("Abgeschlossenes Studium im Wirtschaftsingenieurwesen", cleaned)
        self.assertIn("Fundierte Kenntnisse im deutschen Arbeitsrecht und in SAP MM", cleaned)
        self.assertIn("Führerschein Klasse C1 und Staplerschein", cleaned)
        self.assertNotIn("Deutschkenntnisse", cleaned)
        self.assertNotIn("Verhandlungssicheres Deutsch", cleaned)
        self.assertNotIn("Arbeitserlaubnis", cleaned)
        self.assertNotIn("Arbeitsbewilligung", cleaned)
        self.assertNotIn("Sprachkenntnisse", cleaned)

    def test_preserves_non_language_requirements_and_mixed_semicolon_clauses(self) -> None:
        non_lang_sentences = [
            "5+ years of experience with Python, Django and PostgreSQL",
            "Build large language models and evaluation pipelines for search",
            "Process Visa and Mastercard settlement files daily",
            "Experience in B2B SaaS enterprise sales and account management",
            "Verantwortung für Backlog und Roadmap der Authorisierungs- und Abrechnungsdienste",
            "Mindestens fünf Jahre Berufserfahrung im Projektmanagement in der Fertigung",
            "Führerschein Klasse B und Staplerschein erforderlich",
            "Good knowledge of German GAAP (HGB) and IFRS reporting",
            "Kenntnisse in Programmiersprachen wie Python oder Go",
            "We polish our UX prototypes before customer interviews",
        ]
        for sentence in non_lang_sentences:
            self.assertFalse(
                is_lang_visa_sentence(sentence),
                f"Non-language/non-visa sentence falsely flagged: {sentence!r}",
            )

        # Semicolon-separated mixed bullet keeps non-language clause.
        mixed = "- Forklift licence; good German\n- Knowledge of IATF 16949; English B2 required"
        cleaned, removed = strip_lang_visa_text(mixed)
        self.assertEqual(removed, ["good German", "English B2 required"])
        self.assertEqual(cleaned, "- Forklift licence\n- Knowledge of IATF 16949")

        # Paragraph with duty sentence + language sentence keeps the duty sentence.
        para = "Lead a shift team in a logistics hub. German is the team language; English at A2 is enough."
        cleaned_para, removed_para = strip_lang_visa_text(para)
        self.assertEqual(cleaned_para, "Lead a shift team in a logistics hub.")
        self.assertEqual(len(removed_para), 2)

    def test_ablates_structured_raw_metadata_without_mutating_input(self) -> None:
        job = {
            "id": "job_meta_1",
            "title": "Backend Engineer",
            "description": "Build distributed services in Python.\n- 3+ years Python",
            "raw_metadata": {
                "languages": ["German C1", "English B2"],
                "visa_sponsorship": False,
                "qualifications": [
                    "Bachelor's degree in Computer Science",
                    "Fluent German (C1) required",
                    {"text": "Valid work permit for Germany"},
                    {"text": "Experience with Kubernetes"},
                ],
                "detail_requirements": {
                    "skills": ["PostgreSQL", "Sehr gute Deutschkenntnisse"],
                    "arbeitserlaubnis": "required",
                },
                "source": "example_ats",
            },
        }
        snapshot = json.loads(json.dumps(job))
        ablated, changed = ablate_lang_visa(job)
        self.assertTrue(changed)
        # Original job is never mutated.
        self.assertEqual(job, snapshot)
        # Description unchanged because it had no lang/visa clauses.
        self.assertEqual(ablated["description"], job["description"])
        # Structured lang/visa keys and items removed; non-lang items preserved.
        meta = ablated["raw_metadata"]
        self.assertNotIn("languages", meta)
        self.assertNotIn("visa_sponsorship", meta)
        self.assertEqual(meta["source"], "example_ats")
        self.assertEqual(
            meta["qualifications"],
            ["Bachelor's degree in Computer Science", {"text": "Experience with Kubernetes"}],
        )
        self.assertEqual(meta["detail_requirements"]["skills"], ["PostgreSQL"])
        self.assertNotIn("arbeitserlaubnis", meta["detail_requirements"])

        # Job without any language/visa clauses returns changed=False.
        clean_job = {
            "id": "job_clean",
            "title": "Data Analyst",
            "description": "Analyse warehouse throughput using SQL and Tableau.",
        }
        ablated_clean, clean_changed = ablate_lang_visa(clean_job)
        self.assertFalse(clean_changed)
        self.assertEqual(ablated_clean, clean_job)


class _RegressionGates:
    """Gate checks shared by the public (always) and private (when present) datasets."""

    dataset = "public"

    def _load_v3_baseline(self) -> dict:
        _require_dataset(self, self.dataset)
        path = baseline_path(self.dataset)
        if not path.exists():
            if self.dataset == "private":
                self.skipTest(
                    f"No private baseline at {path}; create it with "
                    "`python -m tests.eval.run --dataset private --write-baseline`"
                )
            self.fail(f"Missing baseline file: {path}")
        baselines = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("v3", baselines, f"No 'v3' baseline in {path}")
        return baselines["v3"]

    def _gated_tags(self, baseline: dict) -> list[str]:
        """Hard-hide categories gated by the baseline that a never-hiding matcher can regress."""
        return [
            tag
            for tag in HARD_HIDE_TAGS
            if int(((baseline.get("hard_hide_recall") or {}).get(tag) or {}).get("count", 0))
            >= MIN_HARD_HIDE_GATE_CASES
            and float(((baseline.get("hard_hide_recall") or {}).get(tag) or {}).get("hide_recall", 0.0)) > 0.02
        ]

    def test_matcher_meets_baseline_gates(self) -> None:
        _require_dataset(self, self.dataset)
        matcher_name = os.environ.get("RADIUS_EVAL_MATCHER", "v3").strip() or "v3"
        path = baseline_path(self.dataset)
        if not path.exists():
            self._load_v3_baseline()  # skips (private) or fails (public) with a clear message
        baselines = json.loads(path.read_text(encoding="utf-8"))
        baseline_key = matcher_name if matcher_name in baselines else "v3"
        self.assertIn(
            baseline_key,
            baselines,
            f"No baseline recorded for matcher {matcher_name!r} (or 'v3') in {path}",
        )

        report = evaluate(matcher_name, dataset=self.dataset, split="all")
        failures = check_regression_gates(report, baselines[baseline_key], min_pairs=MIN_PAIR_COUNT[self.dataset])
        self.assertFalse(failures, "Regression gate failure(s):\n- " + "\n- ".join(failures))

    def test_target_matchers_meet_target_gates(self) -> None:
        _require_dataset(self, self.dataset)
        raw = os.environ.get("RADIUS_EVAL_TARGET_MATCHERS", "")
        target_matchers = [item.strip() for item in raw.split(",") if item.strip()]
        if not target_matchers:
            self.skipTest(
                "RADIUS_EVAL_TARGET_MATCHERS is unset/empty; set e.g. RADIUS_EVAL_TARGET_MATCHERS=v3 "
                "to run target acceptance gates"
            )
        all_failures: list[str] = []
        for matcher_name in target_matchers:
            report = evaluate(matcher_name, dataset=self.dataset, split="all")
            for failure in check_target_gates(report):
                all_failures.append(f"[{self.dataset}/{matcher_name}] {failure}")
        self.assertFalse(all_failures, "Target gate failure(s):\n- " + "\n- ".join(all_failures))

    def test_constant_50_matcher_fails_gates(self) -> None:
        baseline = self._load_v3_baseline()
        name = f"_test_constant_50_{self.dataset}"
        register_matcher(name, lambda _job, _profile: {"score": 50.0, "eligible": True})
        try:
            report = evaluate(name, dataset=self.dataset, split="all")
            failures = check_regression_gates(report, baseline)
            self.assertTrue(failures, "Constant-50 matcher must fail regression gates")
            joined = "\n".join(failures)
            self.assertIn("hide_recall", joined)
            self.assertIn("pairwise_auc", joined)
            self.assertIn("nDCG@10", joined)
            gated_tags = self._gated_tags(baseline)
            self.assertTrue(gated_tags, "Expected at least one gated hard-hide category in baseline")
            for tag in gated_tags:
                self.assertIn(tag, joined)
        finally:
            MATCHERS.pop(name, None)
            MATCHER_VERSIONS.pop(name, None)

    def test_everything_visible_matcher_fails_gates(self) -> None:
        baseline = self._load_v3_baseline()
        v3 = get_matcher("v3")
        name = f"_test_everything_visible_{self.dataset}"
        register_matcher(
            name,
            lambda job, profile: {**v3(job, profile), "eligible": True},
        )
        try:
            report = evaluate(name, dataset=self.dataset, split="all")
            self.assertEqual(report["overall"]["hide_recall"], 0.0)
            failures = check_regression_gates(report, baseline)
            self.assertTrue(failures, "Everything-visible matcher must fail regression gates")
            joined = "\n".join(failures)
            self.assertIn("hide_recall regressed", joined)
            gated_tags = self._gated_tags(baseline)
            self.assertTrue(gated_tags, "Expected at least one gated hard-hide category in baseline")
            for tag in gated_tags:
                self.assertIn(tag, joined)
        finally:
            MATCHERS.pop(name, None)
            MATCHER_VERSIONS.pop(name, None)


class TestPublicEvalRegression(_RegressionGates, unittest.TestCase):
    dataset = "public"

    def test_public_set_keeps_translation_pair_coverage(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["v3"]["overall"]
        self.assertGreaterEqual(baseline["pair_count"], MIN_PAIR_COUNT["public"])
        self.assertIsNotNone(baseline["de_en_parity"])
        self.assertIsNotNone(baseline["pair_eligibility_disagreement"])
        stub = {"overall": {"pair_count": 0, "de_en_parity": None, "pair_eligibility_disagreement": None,
                            **{key: 0.5 for key in ("ndcg_at_10", "pairwise_auc", "hide_precision", "hide_recall",
                                                    "good_hidden_rate", "band_error")}}}
        failures = check_regression_gates(stub, {"overall": dict(baseline)}, min_pairs=MIN_PAIR_COUNT["public"])
        self.assertTrue(any("report has 0 DE/EN pairs" in item for item in failures), failures)

    def test_every_public_pair_has_both_languages_and_a_shared_split(self) -> None:
        cases = load_cases("public")
        by_pair: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        for case in cases:
            if case["pair_id"]:
                by_pair[(case["profile_id"], case["pair_id"])][case["lang"]] = case
        self.assertGreaterEqual(len(by_pair), MIN_PAIR_COUNT["public"])
        for key, langs in by_pair.items():
            self.assertEqual(set(langs), {"de", "en"}, key)
            self.assertEqual(langs["de"]["split"], langs["en"]["split"], key)
            self.assertEqual(langs["de"]["label"]["grade"], langs["en"]["label"]["grade"], key)
            self.assertEqual(langs["de"]["label"]["should_hide"], langs["en"]["label"]["should_hide"], key)

    def test_committed_baseline_holds_public_set_only(self) -> None:
        baselines = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        public_profiles = set(load_profiles("public"))
        for key, entry in baselines.items():
            self.assertEqual(entry.get("dataset"), "public", f"Baseline {key!r} is not a public-set baseline")
            self.assertLessEqual(
                set(entry.get("per_profile") or {}),
                public_profiles,
                f"Baseline {key!r} lists profiles that are not in the public set",
            )


class TestPrivateEvalRegression(_RegressionGates, unittest.TestCase):
    """Skipped unless the private real-CV dataset (and its baseline) exist locally."""

    dataset = "private"


if __name__ == "__main__":
    unittest.main()
