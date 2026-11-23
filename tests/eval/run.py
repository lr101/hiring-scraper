"""Offline evaluation runner for Radius job matchers.

Usage:
    python -m tests.eval.run [--dataset public|private] [--matcher v1] [--split all|dev|heldout]
                             [--json out.json] [--write-baseline]

``public`` (default) is the committed fictional composite set in ``tests/eval/public``; its
baselines live in ``tests/eval/baseline.json``. ``private`` is the gitignored real-CV set in
``data/eval/cv`` (or ``RADIUS_PRIVATE_EVAL_DIR``); its baseline is written next to the data and
never committed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import numbers
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from hiring_scraper.matching import VERSION as V1_VERSION
from hiring_scraper.matching import match_job
from hiring_scraper.matching_v2 import VERSION as V2_VERSION
from hiring_scraper.matching_v2 import match_job_v2
from tests.eval import cv as private_dataset
from tests.eval import public as public_dataset
from tests.eval.ablation import ablate_lang_visa
from tests.eval.metrics import (
    GRADE_SCORE_BANDS,
    GRADE_SCORE_TARGETS,
    band_error,
    de_en_parity,
    effective_ranking_score,
    expected_calibration_error,
    good_hidden_rate,
    hide_precision,
    hide_recall,
    lang_visa_ablation_metrics,
    ndcg_at_k,
    pair_eligibility_disagreement,
    pairwise_auc,
    precision_at_k,
    rank_indices,
    ranking_auc,
    ranking_relevance,
    score_range_metrics,
    score_spread,
)
from tests.eval.semantic import build_semantic_evidence

EVAL_DIR = Path(__file__).resolve().parent
BASELINE_PATH = EVAL_DIR / "baseline.json"  # public-set baselines only (committed)
DATASETS = {"public": public_dataset, "private": private_dataset}
DEFAULT_DATASET = "public"

HARD_HIDE_TAGS: tuple[str, ...] = (
    "hard_hide_cefr",
    "hard_hide_excluded_term",
    "hard_hide_declined_onsite",
    "hard_hide_work_permit",
)
VALID_SPLITS: tuple[str, ...] = ("dev", "heldout", "all")

MatcherFn = Callable[[dict, dict], dict]
MATCHERS: dict[str, MatcherFn] = {}
MATCHER_VERSIONS: dict[str, str] = {}


def assign_split(case_id: str, pair_id: str | None = None) -> str:
    """Deterministically assign a case/group key to 'dev' (~70%) or 'heldout' (~30%).

    Hashes ``pair_id`` when present (otherwise ``case_id``) so grouped items
    stay in the same split.
    """
    key = pair_id if pair_id else case_id
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return "heldout" if bucket < 30 else "dev"


def register_matcher(name: str, fn: MatcherFn, *, version: str = "") -> None:
    """Register a pluggable matcher callable(job, profile) -> result dict."""
    MATCHERS[name] = fn
    if version:
        MATCHER_VERSIONS[name] = version


register = register_matcher


def get_matcher(name: str) -> MatcherFn:
    if name not in MATCHERS:
        available = ", ".join(sorted(MATCHERS)) or "(none)"
        raise KeyError(f"Unknown matcher {name!r}; registered matchers: {available}")
    return MATCHERS[name]


register_matcher("v1", lambda job, profile: match_job(job, profile), version=V1_VERSION)
register_matcher(
    "v1+sem",
    lambda job, profile: match_job(job, profile, semantic=build_semantic_evidence(job, profile)),
    version=f"{V1_VERSION}+fixture-sem",
)
register_matcher("v2", lambda job, profile: match_job_v2(job, profile), version=V2_VERSION)
register_matcher(
    "v2+sem",
    lambda job, profile: match_job_v2(job, profile, semantic=build_semantic_evidence(job, profile, aggregate=True)),
    version=f"{V2_VERSION}+fixture-sem",
)


def get_dataset(name: str = DEFAULT_DATASET):
    """Return the dataset module (``load_cases``/``load_profiles``/``is_available``) for ``name``."""
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset {name!r}; expected one of {sorted(DATASETS)}")
    return DATASETS[name]


def dataset_available(name: str = DEFAULT_DATASET) -> bool:
    return bool(get_dataset(name).is_available())


def baseline_path(dataset: str = DEFAULT_DATASET) -> Path:
    """Public baselines are committed; the private baseline sits next to the private data."""
    return BASELINE_PATH if dataset == "public" else private_dataset.BASELINE_PATH


def load_profiles(dataset: str = DEFAULT_DATASET, profiles_dir: Path | None = None) -> dict[str, dict]:
    """Load evaluation profiles keyed by profile_id."""
    module = get_dataset(dataset)
    return module.load_profiles(profiles_dir) if profiles_dir else module.load_profiles()


def load_cases(
    dataset: str = DEFAULT_DATASET,
    *,
    split: str = "all",
    profiles_dir: Path | None = None,
    jobs_dir: Path | None = None,
    labels_path: Path | None = None,
) -> list[dict]:
    """Load evaluation cases from the selected dataset."""
    module = get_dataset(dataset)
    kwargs = {
        key: value
        for key, value in (("profiles_dir", profiles_dir), ("jobs_dir", jobs_dir), ("labels_path", labels_path))
        if value is not None
    }
    return module.load_cases(split=split, **kwargs)


def evaluate_clause_removal(matcher_name: str) -> list[dict]:
    """Measure individually authored posting pairs, independently of clause detection."""
    matcher = get_matcher(matcher_name)
    profile = {
        "desired_roles": ["Platform Engineer", "Plattformingenieur"],
        "skills": ["Terraform", "Kubernetes", "SQL", "PostgreSQL", "Grafana", "Python", "Docker", "Jira", "Agile"],
        "language_levels": {"German": "B2", "English": "C2", "French": "C1", "Spanish": "B2"},
        "application_preferences": {"authorization_country": "Germany"},
    }
    results = []
    with (EVAL_DIR / "public" / "clause_removal.tsv").open(encoding="utf-8", newline="") as fixture:
        for index, row in enumerate(csv.DictReader(fixture, delimiter="\t"), 1):
            title = "Platform Engineer" if row["lang"] == "en" else "Plattformingenieur"
            original, removed = (
                matcher({"title": title, "description": row[key], "location_text": "Berlin, Germany"}, profile)
                for key in ("with", "without")
            )
            results.append({
                "id": index,
                "kind": row["kind"],
                "lang": row["lang"],
                "visible": original["eligible"] and removed["eligible"],
                "original": original["score"],
                "removed": removed["score"],
            })
    return results


def _validate_matcher_result(
    result: object,
    *,
    case_id: str,
    matcher_name: str,
) -> tuple[float, bool]:
    """Validate matcher output: finite numeric score in [0, 100] and real bool eligible."""
    if not isinstance(result, dict):
        raise ValueError(
            f"Matcher {matcher_name!r} returned non-dict result {type(result).__name__} for case {case_id!r}"
        )
    if "score" not in result:
        raise ValueError(
            f"Matcher {matcher_name!r} result missing 'score' for case {case_id!r}"
        )
    if "eligible" not in result:
        raise ValueError(
            f"Matcher {matcher_name!r} result missing 'eligible' for case {case_id!r}"
        )

    raw_score = result["score"]
    if isinstance(raw_score, bool) or not isinstance(raw_score, numbers.Real):
        raise ValueError(
            f"Matcher {matcher_name!r} returned invalid score {raw_score!r} "
            f"({type(raw_score).__name__}) for case {case_id!r}; expected finite number in [0, 100]"
        )
    score = float(raw_score)
    if not math.isfinite(score) or score < 0.0 or score > 100.0:
        raise ValueError(
            f"Matcher {matcher_name!r} returned out-of-range or non-finite score {raw_score!r} "
            f"for case {case_id!r}; expected finite number in [0, 100]"
        )

    raw_eligible = result["eligible"]
    if type(raw_eligible) is not bool:
        raise ValueError(
            f"Matcher {matcher_name!r} returned non-bool eligible {raw_eligible!r} "
            f"({type(raw_eligible).__name__}) for case {case_id!r}; expected bool"
        )

    return score, raw_eligible


def _round_metrics(metrics: dict) -> dict:
    rounded = {}
    for key, value in metrics.items():
        if isinstance(value, float):
            rounded[key] = round(value, 4)
        elif isinstance(value, dict):
            rounded[key] = _round_metrics(value)
        else:
            rounded[key] = value
    return rounded


def _pair_scores(rows: list[dict]) -> list[tuple[float, float]]:
    by_pair: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        pair_id = row.get("pair_id")
        if pair_id:
            by_pair[(str(row.get("profile_id")), pair_id)][row["lang"]] = float(row["score"])
    pairs: list[tuple[float, float]] = []
    for pair_id in sorted(by_pair):
        langs = by_pair[pair_id]
        if "de" in langs and "en" in langs:
            pairs.append((langs["de"], langs["en"]))
    return pairs


def _pair_eligibilities(rows: list[dict]) -> list[tuple[bool, bool]]:
    by_pair: dict[tuple[str, str], dict[str, bool]] = defaultdict(dict)
    for row in rows:
        pair_id = row.get("pair_id")
        if pair_id:
            by_pair[(str(row.get("profile_id")), pair_id)][row["lang"]] = bool(row["eligible"])
    pairs: list[tuple[bool, bool]] = []
    for pair_id in sorted(by_pair):
        langs = by_pair[pair_id]
        if "de" in langs and "en" in langs:
            pairs.append((langs["de"], langs["en"]))
    return pairs


def _compute_language_metrics(rows: list[dict]) -> dict[str, dict]:
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("lang"):
            by_lang[str(row["lang"])].append(row)

    result: dict[str, dict] = {}
    for lang in sorted(by_lang):
        l_rows = by_lang[lang]
        grades = [r["grade"] for r in l_rows]
        scores = [float(r["score"]) for r in l_rows]
        hidden = [not bool(r["eligible"]) for r in l_rows]
        case_ids = [r["id"] for r in l_rows]

        should_hide = [bool(r["should_hide"]) for r in l_rows]
        relevance = ranking_relevance(grades, should_hide)

        order = rank_indices(scores, hidden, case_ids=case_ids)
        ranked_grades = [relevance[idx] for idx in order]
        ranked_hidden = [hidden[idx] for idx in order]
        eff_scores = [effective_ranking_score(s, h) for s, h in zip(scores, hidden)]

        mean_by_grade: dict[str, float | None] = {}
        count_by_grade: dict[str, int] = {}
        for g in (0, 1, 2, 3):
            g_scores = [float(r["score"]) for r in l_rows if r["grade"] == g]
            count_by_grade[str(g)] = len(g_scores)
            mean_by_grade[str(g)] = (
                round(sum(g_scores) / len(g_scores), 2) if g_scores else None
            )

        result[lang] = _round_metrics(
            {
                "count": len(l_rows),
                "ndcg_at_10": ndcg_at_k(ranked_grades, k=10, hidden_in_ranked_order=ranked_hidden),
                "pairwise_auc": ranking_auc(grades, eff_scores, should_hide),
                "mean_score": sum(scores) / len(scores) if scores else 0.0,
                "mean_score_by_grade": mean_by_grade,
                "count_by_grade": count_by_grade,
            }
        )
    return result


def _lang_auc_gap(by_language: dict[str, dict]) -> float | None:
    if "de" in by_language and "en" in by_language:
        return round(
            abs(float(by_language["de"]["pairwise_auc"]) - float(by_language["en"]["pairwise_auc"])),
            4,
        )
    return None


def _ablation_pairs(rows: list[dict]) -> list[tuple[float, float]]:
    return [
        (float(r["score"]), float(r["ablated_score"]))
        for r in rows
        if r["eligible"] and r.get("lang_visa_ablated") and r.get("ablated_score") is not None
    ]


def _compute_profile_metrics(rows: list[dict]) -> dict:
    grades = [r["grade"] for r in rows]
    scores = [float(r["score"]) for r in rows]
    should_hide = [bool(r["should_hide"]) for r in rows]
    hidden = [not bool(r["eligible"]) for r in rows]
    case_ids = [r["id"] for r in rows]

    relevance = ranking_relevance(grades, should_hide)
    order = rank_indices(scores, hidden, case_ids=case_ids)
    ranked_grades = [relevance[idx] for idx in order]
    ranked_hidden = [hidden[idx] for idx in order]
    eff_scores = [effective_ranking_score(s, h) for s, h in zip(scores, hidden)]
    score_pairs = _pair_scores(rows)
    elig_pairs = _pair_eligibilities(rows)
    spread = score_spread(scores)
    range_m = score_range_metrics(grades, scores, is_hidden=hidden)
    ablation_m = lang_visa_ablation_metrics(_ablation_pairs(rows))
    return _round_metrics(
        {
            "count": len(rows),
            "pair_count": len(score_pairs),
            "ndcg_at_10": ndcg_at_k(ranked_grades, k=10, hidden_in_ranked_order=ranked_hidden),
            "precision_at_10": precision_at_k(ranked_grades, k=10, hidden_in_ranked_order=ranked_hidden),
            "pairwise_auc": ranking_auc(grades, eff_scores, should_hide),
            "hide_precision": hide_precision(should_hide, hidden),
            "hide_recall": hide_recall(should_hide, hidden),
            "good_hidden_rate": good_hidden_rate(grades, hidden),
            "de_en_parity": de_en_parity(score_pairs) if score_pairs else None,
            "pair_eligibility_disagreement": (
                pair_eligibility_disagreement(elig_pairs) if elig_pairs else None
            ),
            "band_error": band_error(grades, scores, is_hidden=hidden),
            "ece": expected_calibration_error(grades, scores, is_hidden=hidden),
            **range_m,
            **ablation_m,
            "score_spread": spread,
        }
    )


def _compute_hard_hide_recall(rows: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for tag in HARD_HIDE_TAGS:
        tag_rows = [r for r in rows if tag in r["tags"]]
        if not tag_rows:
            continue
        t_should_hide = [r["should_hide"] for r in tag_rows]
        t_hidden = [not r["eligible"] for r in tag_rows]
        result[tag] = _round_metrics(
            {
                "count": len(tag_rows),
                "hide_recall": hide_recall(t_should_hide, t_hidden),
            }
        )
    return result


def _compute_aggregate_metrics(
    rows: list[dict],
    per_profile: dict[str, dict] | None = None,
    by_language: dict[str, dict] | None = None,
) -> dict:
    if per_profile is None:
        by_prof: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_prof[row["profile_id"]].append(row)
        per_profile = {pid: _compute_profile_metrics(by_prof[pid]) for pid in sorted(by_prof)}
    if by_language is None:
        by_language = _compute_language_metrics(rows)

    profile_list = list(per_profile.values())
    all_grades = [r["grade"] for r in rows]
    all_scores = [r["score"] for r in rows]
    all_should_hide = [r["should_hide"] for r in rows]
    all_hidden = [not r["eligible"] for r in rows]
    score_pairs = _pair_scores(rows)
    elig_pairs = _pair_eligibilities(rows)
    range_m = score_range_metrics(all_grades, all_scores, is_hidden=all_hidden)
    ablation_m = lang_visa_ablation_metrics(_ablation_pairs(rows))

    return _round_metrics(
        {
            "count": len(rows),
            "pair_count": len(score_pairs),
            "should_hide": sum(1 for x in all_should_hide if x),
            "ndcg_at_10": (
                sum(p["ndcg_at_10"] for p in profile_list) / len(profile_list)
                if profile_list
                else 0.0
            ),
            "precision_at_10": (
                sum(p["precision_at_10"] for p in profile_list) / len(profile_list)
                if profile_list
                else 0.0
            ),
            "pairwise_auc": (
                sum(p["pairwise_auc"] for p in profile_list) / len(profile_list)
                if profile_list
                else 0.5
            ),
            "hide_precision": hide_precision(all_should_hide, all_hidden),
            "hide_recall": hide_recall(all_should_hide, all_hidden),
            "good_hidden_rate": good_hidden_rate(all_grades, all_hidden),
            "de_en_parity": de_en_parity(score_pairs) if score_pairs else None,
            "pair_eligibility_disagreement": (
                pair_eligibility_disagreement(elig_pairs) if elig_pairs else None
            ),
            "lang_auc_gap": _lang_auc_gap(by_language),
            "band_error": band_error(all_grades, all_scores, is_hidden=all_hidden),
            "ece": expected_calibration_error(all_grades, all_scores, is_hidden=all_hidden),
            **range_m,
            **ablation_m,
            "score_spread": score_spread(all_scores),
        }
    )


def evaluate(
    matcher_name: str = "v1",
    *,
    dataset: str = DEFAULT_DATASET,
    split: str = "all",
    profiles_dir: Path | None = None,
    jobs_dir: Path | None = None,
    labels_path: Path | None = None,
    include_case_results: bool = False,
) -> dict:
    if split not in VALID_SPLITS:
        raise ValueError(f"Invalid split {split!r}; expected one of {VALID_SPLITS}")

    matcher = get_matcher(matcher_name)
    cases = load_cases(
        dataset,
        split=split,
        profiles_dir=profiles_dir,
        jobs_dir=jobs_dir,
        labels_path=labels_path,
    )

    evaluated_rows: list[dict] = []
    by_profile: dict[str, list[dict]] = defaultdict(list)
    by_split_rows: dict[str, list[dict]] = defaultdict(list)

    lang_counts: Counter[str] = Counter()
    grade_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()

    for case in cases:
        case_split = case.get("split") or assign_split(case["id"], case.get("pair_id"))
        if split != "all" and case_split != split:
            continue
        profile_id = case["profile_id"]
        result = matcher(case["job"], case["profile"])
        score, eligible = _validate_matcher_result(
            result,
            case_id=case["id"],
            matcher_name=matcher_name,
        )
        lang_visa_ablated = False
        ablated_score: float | None = None
        if eligible:
            ablated_job, lang_visa_ablated = ablate_lang_visa(case["job"])
            if lang_visa_ablated:
                ablated_result = matcher(ablated_job, case["profile"])
                ablated_score, _ = _validate_matcher_result(
                    ablated_result,
                    case_id=f"{case['id']}:ablated",
                    matcher_name=matcher_name,
                )
        label = case["label"]
        tags = list(case.get("tags") or [])
        lang = case.get("lang") or case.get("language")
        row = {
            "id": case["id"],
            "profile_id": profile_id,
            "job_id": case.get("job_id"),
            "lang": lang,
            "pair_id": case.get("pair_id"),
            "split": case_split,
            "tags": tags,
            "grade": int(label["grade"]),
            "should_hide": bool(label["should_hide"]),
            "score": score,
            "eligible": eligible,
            "lang_visa_ablated": lang_visa_ablated,
            "ablated_score": ablated_score,
            "fit_tier": result.get("fit_tier"),
            "conflicts": list(result.get("conflicts") or []),
            "unknowns": list(result.get("unknowns") or []),
            "reasons": list(result.get("reasons") or []),
        }
        evaluated_rows.append(row)
        by_profile[profile_id].append(row)
        by_split_rows[case_split].append(row)
        lang_counts[row["lang"]] += 1
        grade_counts[str(row["grade"])] += 1
        profile_counts[profile_id] += 1
        split_counts[case_split] += 1
        for tag in tags:
            tag_counts[tag] += 1

    per_profile = {
        profile_id: _compute_profile_metrics(by_profile[profile_id])
        for profile_id in sorted(by_profile)
    }
    by_language = _compute_language_metrics(evaluated_rows)
    overall = _compute_aggregate_metrics(
        evaluated_rows,
        per_profile=per_profile,
        by_language=by_language,
    )
    hard_hide_recall = _compute_hard_hide_recall(evaluated_rows)

    by_split: dict[str, dict] = {}
    for split_name in ("dev", "heldout"):
        if split_name in by_split_rows:
            split_langs = _compute_language_metrics(by_split_rows[split_name])
            split_metrics = _compute_aggregate_metrics(
                by_split_rows[split_name],
                by_language=split_langs,
            )
            split_metrics["by_language"] = split_langs
            split_metrics["hard_hide_recall"] = _compute_hard_hide_recall(by_split_rows[split_name])
            by_split[split_name] = split_metrics

    by_tag: dict[str, dict] = {}
    for tag in sorted(tag_counts):
        tag_rows = [r for r in evaluated_rows if tag in r["tags"]]
        t_grades = [r["grade"] for r in tag_rows]
        t_scores = [r["score"] for r in tag_rows]
        t_should_hide = [r["should_hide"] for r in tag_rows]
        t_hidden = [not r["eligible"] for r in tag_rows]
        by_tag[tag] = _round_metrics(
            {
                "count": len(tag_rows),
                "mean_score": sum(t_scores) / len(t_scores),
                "hidden_rate": sum(1 for h in t_hidden if h) / len(t_hidden),
                "good_hidden_rate": good_hidden_rate(t_grades, t_hidden),
                "hide_recall": hide_recall(t_should_hide, t_hidden),
            }
        )

    all_pairs = _pair_scores(evaluated_rows)
    all_should_hide = [r["should_hide"] for r in evaluated_rows]

    report = {
        "dataset": dataset,
        "matcher": matcher_name,
        "matcher_version": MATCHER_VERSIONS.get(matcher_name, ""),
        "split": split,
        "calibration_mapping": {
            str(g): {"band": list(GRADE_SCORE_BANDS[g]), "target": GRADE_SCORE_TARGETS[g]}
            for g in sorted(GRADE_SCORE_TARGETS)
        },
        "counts": {
            "total": len(evaluated_rows),
            "profiles": len(per_profile),
            "pairs": len(all_pairs),
            "should_hide": sum(1 for x in all_should_hide if x),
            "by_split": dict(sorted(split_counts.items())),
            "by_language": dict(sorted(lang_counts.items())),
            "by_profile": dict(sorted(profile_counts.items())),
            "by_grade": dict(sorted(grade_counts.items())),
            "by_tag": dict(sorted(tag_counts.items())),
        },
        "overall": overall,
        "by_language": by_language,
        "hard_hide_recall": hard_hide_recall,
        "by_split": by_split,
        "per_profile": per_profile,
        "by_tag": by_tag,
    }
    if include_case_results:
        report["cases"] = evaluated_rows
    return report


def _fmt_opt_float(val: float | None, width: int, prec: int) -> str:
    if val is None:
        return f"{'n/a':>{width}}"
    return f"{val:>{width}.{prec}f}"


def _format_metric_row(label: str, count: int, m: dict) -> str:
    sp = m["score_spread"]
    parity_str = _fmt_opt_float(m.get("de_en_parity"), 6, 2)
    elig_str = _fmt_opt_float(m.get("pair_eligibility_disagreement"), 7, 4)
    return (
        f"{label:<22} {count:>3} {m['ndcg_at_10']:>8.4f} {m['precision_at_10']:>6.4f} "
        f"{m['pairwise_auc']:>6.4f} {m['hide_precision']:>6.4f} {m['hide_recall']:>6.4f} "
        f"{m['good_hidden_rate']:>8.4f} {parity_str} "
        f"{elig_str} {m['band_error']:>7.4f} {m['ece']:>6.4f} "
        f"{sp['std']:>6.2f} {sp['distinct_count']:>4} {sp['plateau_fraction'] * 100:>5.1f}%"
    )


def format_report(report: dict) -> str:
    overall = report["overall"]
    counts = report["counts"]
    split_info = counts.get("by_split") or {}
    header = (
        f"{'Profile':<22} {'N':>3} {'nDCG@10':>8} {'P@10':>6} {'AUC':>6} "
        f"{'HideP':>6} {'HideR':>6} {'GoodHid':>8} {'DE/EN':>6} "
        f"{'EligDis':>7} {'BandErr':>7} {'ECE':>6} {'Std':>6} {'Dist':>4} {'Plat%':>6}"
    )
    sep = "-" * len(header)
    lines = [
        f"Radius Matcher Evaluation — dataset={report.get('dataset', DEFAULT_DATASET)} matcher={report['matcher']}"
        + (f" ({report['matcher_version']})" if report.get("matcher_version") else "")
        + f" | split={report.get('split', 'all')}",
        f"Cases: {counts['total']} (dev={split_info.get('dev', 0)}, heldout={split_info.get('heldout', 0)}) | "
        f"Profiles: {counts['profiles']} | "
        f"DE/EN: {counts['by_language'].get('de', 0)}/{counts['by_language'].get('en', 0)} | "
        f"Pairs: {counts['pairs']} | Should-hide: {counts['should_hide']}",
        "",
        "Per-profile and overall metrics:",
        header,
        sep,
    ]
    for profile_id, m in report["per_profile"].items():
        lines.append(_format_metric_row(profile_id, m["count"], m))
    lines.append(sep)
    lines.append(_format_metric_row("OVERALL", counts["total"], overall))

    if report.get("by_split"):
        lines.append("")
        lines.append("Per-split metrics:")
        lines.append(header.replace("Profile", "Split  ", 1))
        lines.append(sep)
        for split_name, sm in report["by_split"].items():
            lines.append(_format_metric_row(split_name, sm["count"], sm))

    if report.get("by_language"):
        lines.append("")
        lines.append("Per-language metrics (DE vs EN):")
        lines.append(
            f"{'Lang':<6} {'N':>4} {'nDCG@10':>8} {'AUC':>7} {'Mean':>7} "
            f"{'G0':>7} {'G1':>7} {'G2':>7} {'G3':>7}"
        )
        lines.append("-" * 68)
        for lang, lm in report["by_language"].items():
            mg = lm.get("mean_score_by_grade") or {}
            lines.append(
                f"{lang:<6} {lm['count']:>4} {lm['ndcg_at_10']:>8.4f} {lm['pairwise_auc']:>7.4f} "
                f"{lm['mean_score']:>7.2f} "
                f"{_fmt_opt_float(mg.get('0'), 7, 2)} {_fmt_opt_float(mg.get('1'), 7, 2)} "
                f"{_fmt_opt_float(mg.get('2'), 7, 2)} {_fmt_opt_float(mg.get('3'), 7, 2)}"
            )
        gap = overall.get("lang_auc_gap")
        lines.append(f"DE/EN AUC gap: {f'{gap:.4f}' if gap is not None else 'n/a'}")

    sp = overall["score_spread"]
    lines.append("")
    lines.append(f"Plateau counts (29/59/85): {sp['plateau_counts']}")

    lines.append("")
    lines.append("Score range utilisation (visible cases):")
    lines.append(
        f"  strong_reach_80={overall['strong_reach_80']:.4f} | "
        f"good_in_band={overall['good_in_band']:.4f} | "
        f"irrelevant_below_30={overall['irrelevant_below_30']:.4f} | "
        f"band_error={overall['band_error']:.4f}"
    )
    lines.append(
        f"  max_value_share={overall['max_value_share']:.4f} | "
        f"spread_p5_p95={overall['spread_p5_p95']:.2f} | "
        f"max_score={overall['max_score']:.1f}"
    )

    lines.append("")
    lines.append(
        f"Language/visa ablation (visible ablated cases={overall['lang_visa_ablated_count']}):"
    )
    lines.append(
        f"  mean_abs_delta={overall['lang_visa_mean_abs_delta']:.4f} | "
        f"max_delta={overall['lang_visa_max_delta']:.2f} | "
        f"band_jumps={overall['lang_visa_band_jumps']:.4f}"
    )

    if report.get("hard_hide_recall"):
        lines.append("")
        lines.append("Hard-hide recall by category:")
        lines.append(f"{'Category':<28} {'N':>4} {'HideRec':>8}")
        lines.append("-" * 42)
        for tag, hm in report["hard_hide_recall"].items():
            lines.append(f"{tag:<28} {hm['count']:>4} {hm['hide_recall']:>8.4f}")

    if report.get("by_tag"):
        lines.append("")
        lines.append("Slice breakdown by tag:")
        lines.append(f"{'Tag':<34} {'N':>4} {'MeanScore':>10} {'Hidden%':>8} {'GoodHid%':>9} {'HideRec':>8}")
        lines.append("-" * 79)
        for tag, tm in report["by_tag"].items():
            lines.append(
                f"{tag:<34} {tm['count']:>4} {tm['mean_score']:>10.2f} "
                f"{tm['hidden_rate'] * 100:>7.1f}% {tm['good_hidden_rate'] * 100:>8.1f}% "
                f"{tm['hide_recall']:>8.4f}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline evaluation harness for Radius job matcher.")
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        choices=sorted(DATASETS),
        help="Evaluation dataset: public (committed composites, default) or private (gitignored real CVs)",
    )
    parser.add_argument("--matcher", default="v1", help="Registered matcher name (default: v1)")
    parser.add_argument(
        "--split",
        default="all",
        choices=VALID_SPLITS,
        help="Dataset split to evaluate: dev, heldout, or all (default: all)",
    )
    parser.add_argument("--json", dest="json_out", default=None, help="Optional path to write JSON report")
    parser.add_argument("--write-baseline", action="store_true", help="Write baseline metrics (public: tests/eval/baseline.json; private: next to the private data)")
    parser.add_argument(
        "--baseline-key",
        default=None,
        help="Key under which to store metrics in baseline.json (defaults to --matcher)",
    )
    parser.add_argument("--include-cases", action="store_true", help="Include per-case predictions in --json output")
    args = parser.parse_args(argv)

    if args.write_baseline and args.split != "all":
        parser.error("--write-baseline requires --split all so full baseline metrics are recorded.")

    if not dataset_available(args.dataset):
        message = (
            f"Dataset {args.dataset!r} is not available on this machine "
            f"({private_dataset.CV_EVAL_DIR}); skipping."
        )
        print(message, file=sys.stderr)
        return 2 if args.write_baseline else 0

    report = evaluate(
        args.matcher,
        dataset=args.dataset,
        split=args.split,
        include_case_results=bool(args.include_cases and args.json_out),
    )
    print(format_report(report))

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.write_baseline:
        target = baseline_path(args.dataset)
        existing: dict = {}
        if target.exists():
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "overall" not in loaded:
                existing = loaded
        key = args.baseline_key or args.matcher
        existing[key] = {k: v for k, v in report.items() if k != "cases"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {args.dataset} baseline for {key!r} to {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
