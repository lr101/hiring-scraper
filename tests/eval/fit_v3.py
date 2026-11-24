"""Fit v3 component weights and the monotone calibration on the DEV split only.

Uses the recorded model outputs (``tests.eval.v3_fixtures``); the HELDOUT split
stays untouched for the acceptance gates. Prints the constants to paste into
``hiring_scraper/matching_v3/weights.py``.

    python -m tests.eval.fit_v3 [--dataset public] [--samples 4000]
"""
from __future__ import annotations

import argparse
import random
from collections import Counter

from hiring_scraper.matching_v3 import match_job_v3
from hiring_scraper.matching_v3 import weights as W
from hiring_scraper.matching_v3.score import calibrate, components, raw_score
from tests.eval.metrics import GRADE_SCORE_BANDS, max_value_share_limit, pairwise_auc
from tests.eval.run import _ACTIVE_DATASET, get_dataset, recorded_inputs

NAMES = tuple(W.COMPONENT_WEIGHTS)
IDENTITY = ((0.0, 0.0), (100.0, 100.0))


def case_rows(dataset: str, split: str) -> list[dict]:
    """Component values, fixed penalty total and visibility per labelled case."""
    _ACTIVE_DATASET[0] = dataset
    rows = []
    for case in get_dataset(dataset).load_cases(split=split):
        analysis, evidence = recorded_inputs(case['job'], case['profile'], dataset)
        if analysis is None:
            continue
        result = match_job_v3(case['job'], case['profile'], analysis=analysis, evidence=evidence,
                              knots=IDENTITY, tail_slope=1.0)
        values = components(analysis, evidence)['values']
        base, _ = raw_score(values)
        rows.append({'grade': case['grade'], 'visible': bool(result['eligible']), 'values': values,
                     'offset': result['calibration']['raw'] - base, 'profile': case['profile_id']})
    return rows


def raw_scores(rows: list[dict], weights: dict[str, float]) -> list[float]:
    return [raw_score(row['values'], weights)[0] + row['offset'] for row in rows]


def ranking_quality(rows: list[dict], weights: dict[str, float]) -> float:
    """Mean per-profile pairwise AUC over visible cases (grades as relevance)."""
    scores = raw_scores(rows, weights)
    by_profile: dict[str, tuple[list, list]] = {}
    for row, score in zip(rows, scores):
        if row['visible']:
            grades, values = by_profile.setdefault(row['profile'], ([], []))
            grades.append(row['grade'])
            values.append(score)
    aucs = [pairwise_auc(grades, values) for grades, values in by_profile.values() if len(set(grades)) > 1]
    return sum(aucs) / len(aucs) if aucs else 0.5


def fit_weights(rows: list[dict], samples: int, seed: int = 7) -> dict[str, float]:
    rng = random.Random(seed)
    best, best_quality = dict(W.COMPONENT_WEIGHTS), ranking_quality(rows, W.COMPONENT_WEIGHTS)
    for _ in range(samples):
        draw = [rng.gammavariate(1.0, 1.0) for _ in NAMES]
        total = sum(draw)
        # Keep every component meaningful: no single signal may dominate or vanish.
        candidate = {name: max(0.04, value / total) for name, value in zip(NAMES, draw)}
        norm = sum(candidate.values())
        candidate = {name: round(value / norm, 3) for name, value in candidate.items()}
        # Duty and requirement evidence must carry the score; role and field only refine it.
        if max(candidate.values()) > 0.35 or candidate['duties'] + candidate['must'] + candidate['nice'] < 0.45:
            continue
        quality = ranking_quality(rows, candidate)
        if quality > best_quality + 1e-6:
            best, best_quality = candidate, quality
    return best


def pava(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Isotonic regression of y on x as strictly increasing blocks (x_min, x_max, mean y)."""
    blocks: list[list[float]] = []  # [x_min, x_max, y_sum, count]
    for x, y in sorted(points):
        blocks.append([x, x, y, 1.0])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] >= blocks[-1][2] / blocks[-1][3]:
            x_min, x_max, y_sum, count = blocks.pop()
            blocks[-1][1] = x_max
            blocks[-1][2] += y_sum
            blocks[-1][3] += count
    return [(x_min, x_max, y_sum / count) for x_min, x_max, y_sum, count in blocks]


def ramp_through_blocks(blocks: list[tuple[float, float, float]]) -> list[tuple[float, float]]:
    """Monotone curve without flat stretches: pass through the midpoint between
    neighbouring blocks (in x and in mean y) instead of holding each block's mean, so
    cases pooled into one block still get distinct scores."""
    if len(blocks) == 1:
        return [(blocks[0][0], blocks[0][2]), (blocks[0][1] + 1e-6, blocks[0][2] + 1e-6)]
    points = [(blocks[0][0], blocks[0][2] - (blocks[1][2] - blocks[0][2]) / 2)]
    for left, right in zip(blocks, blocks[1:]):
        points.append(((left[1] + right[0]) / 2, (left[2] + right[2]) / 2))
    points.append((blocks[-1][1], blocks[-1][2] + (blocks[-1][2] - blocks[-2][2]) / 2))
    return points


# Fixed low-end ramp (a design choice, not fitted): jobs with little evidence spread over
# 0-27 instead of sharing one score. Fitted knots start above its last point.
LOW_RAMP: tuple[tuple[float, float], ...] = (
    (-8.0, 0.0), (-2.0, 4.0), (0.0, 10.0), (1.0, 14.0), (2.0, 17.0), (3.0, 20.0), (5.0, 23.0), (7.5, 27.0),
)
BLENDS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)


def fit_knots(rows: list[dict], weights: dict[str, float], count: int = 10,
              blend: float = 0.0, top_margin: float = 2.0) -> tuple[tuple[float, float], ...]:
    """Monotone knots that spread each grade across its score band, in raw-score order.

    Every visible DEV case gets a desired score inside its grade's band (ordered by
    raw score within the grade); isotonic regression of those desired scores on the
    raw score gives a monotone map, sampled at raw-score quantiles above the fixed
    low ramp. ``blend`` mixes in the straight line from the ramp's end to (100, 100),
    which guarantees a minimum slope so distinct raw scores stay apart (no plateaus).
    """
    scores = raw_scores(rows, weights)
    visible = sorted(((score, row['grade']) for row, score in zip(rows, scores) if row['visible']))
    desired: list[tuple[float, float]] = []
    for grade, (low, high) in GRADE_SCORE_BANDS.items():
        members = [score for score, row_grade in visible if row_grade == grade]
        for index, score in enumerate(members):
            fraction = (index + 0.5) / len(members)
            # top_margin lifts strong cases clear of the band edge (strong >= 80 target).
            margin = top_margin if grade == max(GRADE_SCORE_BANDS) else 2.0
            desired.append((score, low + margin + fraction * (high - low - margin - 2)))
    fitted = ramp_through_blocks(pava(desired))
    upper = [score for score, _ in visible if score > LOW_RAMP[-1][0]]
    quantiles = [upper[round(q * (len(upper) - 1))] for q in [i / (count - 1) for i in range(count)]] if upper else []

    def interpolate(x: float) -> float:
        if x <= fitted[0][0]:
            return fitted[0][1]
        for (lx, ly), (rx, ry) in zip(fitted, fitted[1:]):
            if lx <= x <= rx:
                return ly + (x - lx) / (rx - lx or 1) * (ry - ly)
        return fitted[-1][1]

    x0, y0 = LOW_RAMP[-1]

    def line(x: float) -> float:
        return y0 + (x - x0) * (100.0 - y0) / (100.0 - x0)

    knots: list[tuple[float, float]] = list(LOW_RAMP)
    for x in sorted(dict.fromkeys(round(q, 2) for q in quantiles)):
        if x <= knots[-1][0] + 1.0 or x >= 100.0:
            continue
        y = (1 - blend) * max(interpolate(x), y0) + blend * line(x)
        y = max(y, knots[-1][1] + 0.1)  # strictly increasing
        knots.append((x, round(min(99.5, y), 2)))
    knots.append((100.0, 100.0))
    return tuple(knots)


def dev_plateau(rows: list[dict], weights: dict[str, float], knots) -> tuple[float, float]:
    """(max share of visible DEV cases on one integer score, its small-sample limit)."""
    scores = [round(calibrate(raw, knots, W.CALIBRATION_TAIL_SLOPE))
              for row, raw in zip(rows, raw_scores(rows, weights)) if row['visible']]
    share = max(Counter(scores).values()) / len(scores) if scores else 0.0
    return share, max_value_share_limit(len(scores))


def dev_gate_failures(knots) -> list[str]:
    """Target acceptance gates (tests.test_eval_regression) on the DEV split only."""
    from tests.eval.run import MATCHERS, MATCHER_VERSIONS, _v3, evaluate, register_matcher
    from tests.test_eval_regression import check_target_gates
    name = '_fit_v3_candidate'
    register_matcher(name, lambda job, profile: _v3(job, profile, knots=knots))
    try:
        report = evaluate(name, split='dev')
    finally:
        MATCHERS.pop(name, None)
        MATCHER_VERSIONS.pop(name, None)
    return check_target_gates(report['overall'], small_sample=True)


def choose_knots(rows: list[dict], weights: dict[str, float]) -> tuple[tuple[tuple[float, float], ...], dict]:
    """Search a small grid on DEV only: the candidate with the fewest DEV target-gate
    failures wins; ties go to the simplest one (fewest knots, least blending)."""
    best = None
    for count in (10, 16, 24):
        for top_margin in (2.0, 6.0, 10.0):
            for blend in BLENDS:
                knots = fit_knots(rows, weights, count=count, blend=blend, top_margin=top_margin)
                failures = dev_gate_failures(knots)
                if best is None or len(failures) < len(best[1]):
                    best = (knots, failures, {'count': count, 'top_margin': top_margin, 'blend': blend})
                if not failures:
                    return knots, best[2] | {'dev_failures': []}
    return best[0], best[2] | {'dev_failures': best[1]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='public')
    parser.add_argument('--samples', type=int, default=4000)
    parser.add_argument('--keep-weights', action='store_true',
                        help='Fit only the calibration for the current component weights')
    args = parser.parse_args(argv)
    rows = case_rows(args.dataset, 'dev')
    if not rows:
        print('No recorded model outputs; run python -m tests.eval.v3_fixtures first.')
        return 2
    weights = dict(W.COMPONENT_WEIGHTS) if args.keep_weights else fit_weights(rows, args.samples)
    knots, chosen = choose_knots(rows, weights)
    print(f'Chosen on DEV: {chosen}')
    print(f'DEV cases: {len(rows)}; mean per-profile AUC {ranking_quality(rows, weights):.4f} '
          f'(current weights {ranking_quality(rows, W.COMPONENT_WEIGHTS):.4f})')
    print('COMPONENT_WEIGHTS: dict[str, float] = {')
    for name in NAMES:
        print(f"    '{name}': {weights[name]},")
    print('}')
    print('CALIBRATION_KNOTS: tuple[tuple[float, float], ...] = (')
    for x, y in knots:
        print(f'    ({x}, {y}),')
    print(')')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
