"""Regularized DEV-only weight and monotone calibration fitter for matching_v2.

Usage:
    python -m hiring_scraper.matching_v2.fit [--public-only]

Fits the small additive feature weight vector (coordinate search with L2
regularization) and strictly monotone piecewise-linear calibration knots
exclusively on the DEV split (public and, when available, private datasets).
Never uses heldout cases for parameter selection and never prints private text.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Any

from hiring_scraper.matching_v2.calibrate import calibrate_float, calibrate_score, fit_monotone_knots
from hiring_scraper.matching_v2.features import extract_features, precompute_profile
from hiring_scraper.matching_v2.gates import evaluate_gates
from hiring_scraper.matching_v2.score import _lightweight_enrichment, assemble_result, compute_ledger
from hiring_scraper.matching_v2.weights import (
    CALIBRATION_TAIL_SLOPE,
    FEATURE_WEIGHTS,
    VERSION,
)
from tests.eval import cv as private_dataset
from tests.eval import public as public_dataset
from tests.eval.metrics import (
    band_error,
    expected_calibration_error,
    ndcg_at_k,
    pairwise_auc,
    percentile,
    score_range_metrics,
    score_spread,
)
from tests.eval.run import assign_split
from tests.eval.semantic import build_semantic_evidence


# Target scores inside rubric grade bands:
#   grade 0: <30, grade 1: 30-59, grade 2: 60-79, grade 3: 80-100
GRADE_TARGETS: dict[int, float] = {
    0: 6.0,
    1: 42.0,
    2: 72.0,
    3: 95.0,
}

# Prior weights and bounds for regularized coordinate search.
WEIGHT_PRIOR: dict[str, float] = {
    'duty_coverage': 30.0,
    'requirement_coverage': 18.0,
    'skill_overlap': 22.0,
    'role_alignment': 14.0,
    'domain_alignment': 16.0,
    'seniority_fit': 10.0,
    'semantic_similarity': 8.0,
    'domain_mismatch': -24.0,
}

WEIGHT_BOUNDS: dict[str, tuple[float, float]] = {
    'duty_coverage': (8.0, 48.0),
    'requirement_coverage': (4.0, 36.0),
    'skill_overlap': (6.0, 40.0),
    'role_alignment': (2.0, 32.0),
    'domain_alignment': (4.0, 36.0),
    'seniority_fit': (2.0, 24.0),
    'semantic_similarity': (2.0, 24.0),
    'domain_mismatch': (-48.0, -6.0),
}


def _case_split(case: dict[str, Any]) -> str:
    return str(case.get('split') or assign_split(case['id'], case.get('pair_id')))


def _prepare_dataset(
    name: str,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for case in cases:
        job = case['job']
        profile = case['profile']
        sem_ev = build_semantic_evidence(job, profile, aggregate=True)
        sem_sim = float(sem_ev.get('alignment') or 0.0)
        enriched = _lightweight_enrichment(job)
        gate_res = evaluate_gates(job, profile, enriched)
        pc = precompute_profile(profile)
        feat_bundle = extract_features(job, profile, enriched, gate_res, semantic=None, profile_cache=pc)
        prepared.append({
            'dataset': name,
            'id': case['id'],
            'profile_id': case['profile_id'],
            'pair_id': case.get('pair_id'),
            'split': _case_split(case),
            'grade': int(case['grade']),
            'should_hide': bool(case['should_hide']),
            'job': job,
            'profile': profile,
            'enriched': enriched,
            'gate_result': gate_res,
            'feature_bundle': feat_bundle,
            'semantic': None,
            'sem_sim': sem_sim,
        })
    return prepared


def _raw_scores(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[float]:
    return [compute_ledger(r['feature_bundle'], weights=weights)[0] for r in rows]


def _ranking_objective(
    dev_by_dataset: dict[str, list[dict[str, Any]]],
    weights: dict[str, float],
    *,
    l2_reg: float = 0.00004,
    use_semantic: bool = False,
) -> float:
    """Compute regularized DEV ranking + separation objective across datasets."""
    ds_scores: list[float] = []
    for ds_name, rows in dev_by_dataset.items():
        visible = [r for r in rows if r['gate_result']['eligible'] and not r['should_hide']]
        if not visible:
            continue
        raws = []
        for r in visible:
            base_raw = compute_ledger(r['feature_bundle'], weights=weights)[0]
            if use_semantic:
                base_raw += weights['semantic_similarity'] * r['sem_sim']
            raws.append(base_raw)
        by_prof: dict[str, list[tuple[float, int]]] = defaultdict(list)
        pair_map: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r, sc in zip(visible, raws):
            by_prof[r['profile_id']].append((sc, r['grade']))
            if r['pair_id']:
                pair_map[(r['profile_id'], r['pair_id'])].append(sc)

        aucs: list[float] = []
        ndcgs: list[float] = []
        for items in by_prof.values():
            scores = [s for s, _ in items]
            grades = [g for _, g in items]
            auc = pairwise_auc(grades, scores)
            if auc is not None and auc == auc:
                aucs.append(auc)
            order = sorted(range(len(scores)), key=lambda i: -scores[i])
            ndcgs.append(ndcg_at_k([grades[i] for i in order], 10))

        mean_auc = sum(aucs) / len(aucs) if aucs else 0.0
        mean_ndcg = sum(ndcgs) / len(ndcgs) if ndcgs else 0.0

        global_auc = pairwise_auc([r['grade'] for r in visible], raws)
        if global_auc is None or global_auc != global_auc:
            global_auc = 0.0

        pair_gaps = [abs(vals[0] - vals[1]) for vals in pair_map.values() if len(vals) == 2]
        pair_pen = (sum(pair_gaps) / len(pair_gaps)) / 120.0 if pair_gaps else 0.0

        ds_scores.append(0.45 * mean_auc + 0.35 * mean_ndcg + 0.20 * global_auc - 0.12 * pair_pen)

    if not ds_scores:
        return 0.0
    base = sum(ds_scores) / len(ds_scores)
    reg = l2_reg * sum((weights[k] - WEIGHT_PRIOR[k]) ** 2 for k in weights)
    return base - reg


def fit_weights_dev(
    dev_by_dataset: dict[str, list[dict[str, Any]]],
    *,
    optimize_semantic: bool = False,
) -> dict[str, float]:
    """Coordinate search with L2 regularization on DEV splits only."""
    weights = dict(WEIGHT_PRIOR)
    keys = [
        'duty_coverage',
        'requirement_coverage',
        'skill_overlap',
        'role_alignment',
        'domain_alignment',
        'seniority_fit',
        'domain_mismatch',
    ]
    if optimize_semantic:
        keys.append('semantic_similarity')

    best_obj = _ranking_objective(dev_by_dataset, weights)
    for step in (6.0, 3.0, 1.5, 0.5, 0.25):
        improved = True
        rounds = 0
        while improved and rounds < 8:
            improved = False
            rounds += 1
            for k in keys:
                lo, hi = WEIGHT_BOUNDS[k]
                curr = weights[k]
                for delta in (-step, step):
                    cand = round(max(lo, min(hi, curr + delta)), 2)
                    if cand == curr:
                        continue
                    trial = dict(weights)
                    trial[k] = cand
                    obj = _ranking_objective(dev_by_dataset, trial)
                    if obj > best_obj + 1e-6:
                        weights = trial
                        best_obj = obj
                        curr = cand
                        improved = True
    return {k: round(v, 2) for k, v in weights.items()}


def fit_knots_dev(
    dev_by_dataset: dict[str, list[dict[str, Any]]],
    weights: dict[str, float],
    *,
    num_knots: int = 10,
    min_slope: float = 0.40,
    max_slope: float = 2.30,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
) -> tuple[tuple[float, float], ...]:
    """Fit strictly monotone piecewise-linear knots on visible DEV cases only.

    Initializes from PAVA isotonic regression on DEV, then runs regularized
    coordinate refinement (single-knot + monotone prefix/suffix shifts) over
    the knot y-values subject to strict slope bounds ``[min_slope, max_slope]``
    so scores span the full attainable 0-100 range without ties or plateaus.
    """
    xs: list[float] = []
    ys: list[float] = []
    sw: list[float] = []
    ds_eval_data: list[tuple[list[float], list[int]]] = []

    for rows in dev_by_dataset.values():
        visible = [r for r in rows if r['gate_result']['eligible']]
        if not visible:
            continue
        ds_w = 100.0 / len(visible)
        raws = _raw_scores(visible, weights)
        grades = [r['grade'] for r in visible]
        ds_eval_data.append((raws, grades))
        for r, raw in zip(visible, raws):
            xs.append(raw)
            ys.append(GRADE_TARGETS[r['grade']])
            g_mult = 1.30 if r['grade'] in (0, 3) else 1.0
            sw.append(ds_w * g_mult)

    init_knots = fit_monotone_knots(
        xs,
        ys,
        sample_weights=sw,
        num_knots=num_knots,
        min_slope=min_slope,
        max_slope=max_slope,
    )
    anchor_x = [kx for kx, _ in init_knots]
    pava_y = [ky for _, ky in init_knots]
    # Seed the search at the per-dataset band constraints. The quantiles
    # reserve margin for integer rounding and evaluation on unseen postings.
    lower_cutoffs = [percentile([r for r, g in zip(raws, grades) if g == 0], 83)
                     for raws, grades in ds_eval_data if 0 in grades]
    upper_cutoffs = [percentile([r for r, g in zip(raws, grades) if g == 3], 30)
                     for raws, grades in ds_eval_data if 3 in grades]
    band_seed = None
    if lower_cutoffs and upper_cutoffs:
        low, high = round(max(lower_cutoffs), 4), round(min(upper_cutoffs), 4)
        if 50.5 / max_slope <= high - low <= 50.5 / min_slope:
            anchor_x = sorted({
                x for x in anchor_x if min(abs(x - low), abs(x - high)) >= 1.0
            } | {low, high})
            pava_y = [calibrate_float(x, knots=init_knots, tail_slope=tail_slope) for x in anchor_x]
            left_slope = min(0.8, 27.5 / max(1.0, low - anchor_x[0]))
            right_slope = min(0.8, 18.0 / max(1.0, anchor_x[-1] - high))
            if min(left_slope, right_slope) >= min_slope:
                band_seed = [
                    29.0 + left_slope * (x - low) if x < low else
                    79.5 + right_slope * (x - high) if x > high else
                    29.0 + (x - low) * 50.5 / (high - low)
                    for x in anchor_x
                ]

    curr_y = list(pava_y)

    # Features share evidence and do not independently reach 1.0. Use the observed
    # DEV range with headroom, so the strongest jobs can reach 100.
    attainable_top_raw = max(xs) + 0.025 * (max(xs) - min(xs))

    def _valid_slopes(ky_list: list[float]) -> bool:
        if ky_list[0] < 1.5 - 1e-9 or ky_list[-1] > 98.5 + 1e-9:
            return False
        for i in range(1, len(ky_list)):
            dx = anchor_x[i] - anchor_x[i - 1]
            sl = (ky_list[i] - ky_list[i - 1]) / dx
            if sl < min_slope - 1e-6 or sl > max_slope + 1e-6:
                return False
        return True

    def _knot_loss(ky_list: list[float]) -> float:
        cand_knots = tuple(zip(anchor_x, ky_list))
        losses: list[float] = []
        for raws, grades in ds_eval_data:
            cals = [float(calibrate_score(r, knots=cand_knots, tail_slope=tail_slope)) for r in raws]
            be = band_error(grades, cals) or 0.0
            ece = expected_calibration_error(grades, cals) or 0.0
            rng = score_range_metrics(grades, cals)
            i30 = float(rng.get('irrelevant_below_30') or 0.0)
            s80 = float(rng.get('strong_reach_80') or 0.0)
            good_band = float(rng.get('good_in_band') or 0.0)
            msh = float(rng.get('max_value_share') or 0.0)
            p595 = float(rng.get('spread_p5_p95') or 0.0)
            # Penalize violations per DEV dataset so pooling cannot hide a miss.
            # The margin above rollout targets is fixed before evaluating heldout.
            loss = (
                1.0 * be
                + 0.35 * ece
                + 12.0 * max(0.0, 0.83 - i30)
                + 8.0 * max(0.0, 0.70 - s80)
                + 1.2 * (1.0 - good_band)
                + 24.0 * max(0.0, msh - 0.05)
                + 5.0 * max(0.0, msh - 0.03)
                + 0.05 * max(0.0, 65.0 - p595)
            )
            losses.append(loss)
        # Ensure strongest attainable raw scores reach 100 on the calibrated scale.
        top_cal = calibrate_float(attainable_top_raw, knots=cand_knots, tail_slope=tail_slope)
        top_pen = 0.25 * max(0.0, 99.6 - top_cal)
        reg = 0.00004 * sum((a - b) ** 2 for a, b in zip(ky_list, pava_y))
        return (sum(losses) / len(losses) if losses else 0.0) + top_pen + reg

    if band_seed is not None and _valid_slopes(band_seed):
        if not _valid_slopes(curr_y) or _knot_loss(band_seed) < _knot_loss(curr_y):
            curr_y = band_seed
    best_loss = _knot_loss(curr_y)
    n_k = len(curr_y)
    for step in (6.0, 3.0, 1.5, 0.5, 0.25):
        improved = True
        rounds = 0
        while improved and rounds < 15:
            improved = False
            rounds += 1
            moves: list[list[float]] = []
            for i in range(n_k):
                for delta in (-step, step):
                    t1 = list(curr_y)
                    t1[i] += delta
                    moves.append(t1)
                    if i > 0:
                        t2 = list(curr_y)
                        for j in range(i + 1):
                            t2[j] += delta
                        moves.append(t2)
                    if i < n_k - 1:
                        t3 = list(curr_y)
                        for j in range(i, n_k):
                            t3[j] += delta
                        moves.append(t3)
            best_trial = None
            for trial in moves:
                if not _valid_slopes(trial):
                    continue
                loss = _knot_loss(trial)
                if loss < best_loss - 1e-6:
                    best_loss = loss
                    best_trial = trial
            if best_trial is not None:
                curr_y = best_trial
                improved = True

    return tuple((round(ax, 4), round(ay, 4)) for ax, ay in zip(anchor_x, curr_y))


def _summarize_split(
    rows: list[dict[str, Any]],
    weights: dict[str, float],
    knots: tuple[tuple[float, float], ...],
    *,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
) -> dict[str, float]:
    visible_scores: list[float] = []
    visible_grades: list[int] = []
    by_prof: dict[str, list[tuple[float, int]]] = defaultdict(list)

    for r in rows:
        res = assemble_result(
            r['job'],
            r['profile'],
            r['enriched'],
            r['gate_result'],
            r['feature_bundle'],
            semantic=r['semantic'],
            weights=weights,
            knots=knots,
            tail_slope=tail_slope,
            version=VERSION,
        )
        if res['eligible']:
            visible_scores.append(float(res['score']))
            visible_grades.append(r['grade'])
            by_prof[r['profile_id']].append((float(res['score']), r['grade']))

    aucs = [
        a
        for items in by_prof.values()
        if (a := pairwise_auc([g for _, g in items], [s for s, _ in items])) is not None and a == a
    ]
    ndcgs = [
        ndcg_at_k([items[i][1] for i in sorted(range(len(items)), key=lambda i: -items[i][0])], 10)
        for items in by_prof.values()
        if items
    ]
    rng = score_range_metrics(visible_grades, visible_scores)
    return {
        'n_visible': float(len(visible_scores)),
        'ndcg10': sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
        'auc': sum(aucs) / len(aucs) if aucs else 0.0,
        'band_err': band_error(visible_grades, visible_scores) or 0.0,
        'ece': expected_calibration_error(visible_grades, visible_scores) or 0.0,
        'strong_80': rng.get('strong_reach_80') or 0.0,
        'good_band': rng.get('good_in_band') or 0.0,
        'irrel_30': rng.get('irrelevant_below_30') or 0.0,
        'max_share': float(rng.get('max_value_share') or 0.0),
        'p5_p95': float(rng.get('spread_p5_p95') or 0.0),
        'max_score': max(visible_scores) if visible_scores else 0.0,
    }


def run_fit(*, include_private: bool = True, dev_only_report: bool = False) -> dict[str, Any]:
    datasets = {'public': public_dataset}
    if include_private and private_dataset.is_available():
        datasets['private'] = private_dataset
    dev_by_ds = {
        name: _prepare_dataset(name, dataset.load_cases(split='dev'))
        for name, dataset in datasets.items()
    }

    weights = fit_weights_dev(dev_by_ds, optimize_semantic=False)
    knots = fit_knots_dev(dev_by_ds, weights)

    # Fit semantic weight on DEV using precomputed semantic_similarity without re-extracting features.
    best_sem_w = weights['semantic_similarity']
    best_sem_obj = _ranking_objective(dev_by_ds, weights, use_semantic=True)
    for cand in (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0):
        trial = dict(weights)
        trial['semantic_similarity'] = cand
        obj = _ranking_objective(dev_by_ds, trial, use_semantic=True)
        if obj > best_sem_obj + 1e-6:
            best_sem_obj = obj
            best_sem_w = cand
    weights['semantic_similarity'] = round(best_sem_w, 2)

    dev_raws = [
        raw for rows in dev_by_ds.values()
        for raw in _raw_scores(
            [row for row in rows if row['gate_result']['eligible'] and not row['should_hide']], weights
        )
    ]
    best_dev_raw = max(dev_raws)
    attainable_top_raw = best_dev_raw + 0.025 * (best_dev_raw - min(dev_raws))
    report: dict[str, Any] = {
        'weights': weights,
        'knots': knots,
        'best_dev_raw': round(best_dev_raw, 2),
        'attainable_top_raw': round(attainable_top_raw, 2),
        'cal_at_best_dev': calibrate_score(best_dev_raw, knots=knots),
        'cal_at_attainable_top': calibrate_score(attainable_top_raw, knots=knots),
        'metrics': {},
    }
    for name in datasets:
        splits_out = {'dev': _summarize_split(dev_by_ds[name], weights, knots)}
        if not dev_only_report:
            splits_out['heldout'] = _summarize_split(
                _prepare_dataset(name, datasets[name].load_cases(split='heldout')), weights, knots,
            )
        report['metrics'][name] = splits_out
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Fit v2 weights and monotone calibration knots on DEV only.')
    parser.add_argument('--public-only', action='store_true', help='Fit on public DEV only.')
    parser.add_argument('--dev-only', action='store_true', help='Report DEV metrics only (hide heldout).')
    args = parser.parse_args(argv)

    result = run_fit(include_private=not args.public_only, dev_only_report=args.dev_only)
    print('=== FITTED WEIGHTS (DEV ONLY) ===')
    for k, v in result['weights'].items():
        print(f"    '{k}': {v:.2f},")
    print('\n=== FITTED CALIBRATION KNOTS (DEV ONLY) ===')
    for x, y in result['knots']:
        print(f'    ({x:.4f}, {y:.4f}),')
    print(
        f"\nDEV upper raw range {result['best_dev_raw']:.2f}–{result['attainable_top_raw']:.2f} "
        f"-> calibrated {result['cal_at_best_dev']}–{result['cal_at_attainable_top']}"
    )

    print('\n=== METRICS (NO PRIVATE TEXT) ===')
    header = f"{'Dataset':<8} {'Split':<8} {'Nvis':>4} {'nDCG@10':>8} {'AUC':>7} {'BandErr':>8} {'ECE':>7} {'S>=80':>7} {'G_in':>7} {'I<30':>7} {'MaxSh':>7} {'P5-95':>6} {'Max':>5}"
    print(header)
    print('-' * len(header))
    for ds_name, splits in result['metrics'].items():
        for sp_name, m in splits.items():
            print(
                f"{ds_name:<8} {sp_name:<8} {int(m['n_visible']):>4} "
                f"{m['ndcg10']:>8.4f} {m['auc']:>7.4f} {m['band_err']:>8.4f} {m['ece']:>7.4f} "
                f"{m['strong_80']:>7.4f} {m['good_band']:>7.4f} {m['irrel_30']:>7.4f} "
                f"{m['max_share']:>7.4f} {m['p5_p95']:>6.1f} {m['max_score']:>5.0f}"
            )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
