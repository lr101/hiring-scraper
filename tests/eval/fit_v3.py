"""Fit v3 component weights and the monotone calibration on the DEV split only.

Uses the recorded model outputs (``tests.eval.v3_fixtures``); the HELDOUT split
stays untouched for the acceptance gates. Prints the constants to paste into
``hiring_scraper/matching_v3/weights.py``.

    python -m tests.eval.fit_v3 [--dataset public] [--samples 4000]
"""
from __future__ import annotations

import argparse
import random

from hiring_scraper.matching_v3 import match_job_v3
from hiring_scraper.matching_v3 import weights as W
from hiring_scraper.matching_v3.score import components, raw_score
from tests.eval.metrics import GRADE_SCORE_BANDS, pairwise_auc
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


def pava(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Isotonic (non-decreasing) regression of y on x."""
    blocks: list[list[float]] = []  # [x_sum, y_sum, count]
    for x, y in sorted(points):
        blocks.append([x, y, 1.0])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            x2, y2, n2 = blocks.pop()
            blocks[-1][0] += x2
            blocks[-1][1] += y2
            blocks[-1][2] += n2
    return [(x / n, y / n) for x, y, n in blocks]


def fit_knots(rows: list[dict], weights: dict[str, float], count: int = 10) -> tuple[tuple[float, float], ...]:
    """Monotone knots that spread each grade across its score band, in raw-score order.

    Every visible DEV case gets a desired score inside its grade's band (ordered by
    raw score within the grade); isotonic regression of those desired scores on the
    raw score gives a monotone map, sampled at raw-score quantiles. A minimum slope
    keeps distinct raw scores apart, so the 0-100 range is used without plateaus.
    """
    scores = raw_scores(rows, weights)
    visible = sorted(((score, row['grade']) for row, score in zip(rows, scores) if row['visible']))
    desired: list[tuple[float, float]] = []
    for grade, (low, high) in GRADE_SCORE_BANDS.items():
        members = [score for score, row_grade in visible if row_grade == grade]
        for index, score in enumerate(members):
            fraction = (index + 0.5) / len(members)
            desired.append((score, low + 2 + fraction * (high - low - 4)))
    fitted = pava(desired)
    xs = [score for score, _ in visible]
    quantiles = [xs[round(q * (len(xs) - 1))] for q in [i / (count - 1) for i in range(count)]]

    def interpolate(x: float) -> float:
        if x <= fitted[0][0]:
            return fitted[0][1]
        for (lx, ly), (rx, ry) in zip(fitted, fitted[1:]):
            if lx <= x <= rx:
                return ly + (x - lx) / (rx - lx or 1) * (ry - ly)
        return fitted[-1][1]

    knots: list[tuple[float, float]] = [(0.0, 0.0)]
    for x in sorted(dict.fromkeys(round(q, 2) for q in quantiles)):
        if x <= knots[-1][0]:
            continue
        y = max(interpolate(x), knots[-1][1] + 0.35 * (x - knots[-1][0]))
        knots.append((x, round(min(99.5, y), 2)))
    if knots[-1][0] < 100.0:
        knots.append((100.0, 100.0))
    return tuple(knots)


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
    knots = fit_knots(rows, weights)
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
