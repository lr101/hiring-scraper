"""Strictly monotone piecewise-linear (isotonic) score calibration."""
from __future__ import annotations

from collections.abc import Sequence

from hiring_scraper.matching_v2.weights import (
    CALIBRATION_KNOTS,
    CALIBRATION_TAIL_SLOPE,
    VERSION,
)


def validate_knots(knots: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Validate and return strictly increasing (x, y) calibration knots."""
    if len(knots) < 2:
        raise ValueError('Calibration requires at least 2 knots')
    cleaned = [(float(x), float(y)) for x, y in knots]
    for (x0, y0), (x1, y1) in zip(cleaned, cleaned[1:]):
        if x1 <= x0:
            raise ValueError(f'Calibration knot x values must strictly increase: {x0} >= {x1}')
        if y1 < y0:
            raise ValueError(f'Calibration knot y values must be non-decreasing: {y0} > {y1}')
    return cleaned


def calibrate_float(
    raw_score: float,
    *,
    knots: Sequence[tuple[float, float]] = CALIBRATION_KNOTS,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
) -> float:
    """Map raw_score to a continuous calibrated value in [0.0, 100.0]."""
    x = float(raw_score)
    pts = knots if isinstance(knots, tuple) else tuple(knots)
    if len(pts) < 2:
        raise ValueError('Calibration requires at least 2 knots')

    x0, y0 = pts[0]
    if x <= x0:
        val = y0 + (x - x0) * tail_slope
        return max(0.0, min(100.0, val))

    xn, yn = pts[-1]
    if x >= xn:
        val = yn + (x - xn) * tail_slope
        return max(0.0, min(100.0, val))

    for (lx, ly), (rx, ry) in zip(pts, pts[1:]):
        if lx <= x <= rx:
            t = (x - lx) / (rx - lx)
            val = ly + t * (ry - ly)
            return max(0.0, min(100.0, val))

    return max(0.0, min(100.0, yn))


def calibrate_score(
    raw_score: float,
    *,
    knots: Sequence[tuple[float, float]] = CALIBRATION_KNOTS,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
) -> int:
    """Map raw_score monotonically onto an integer in [0, 100]."""
    return int(round(calibrate_float(raw_score, knots=knots, tail_slope=tail_slope)))


def calibration_payload(
    raw_score: float,
    *,
    knots: Sequence[tuple[float, float]] = CALIBRATION_KNOTS,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
    version: str = VERSION,
) -> dict[str, float | int | str]:
    """Return the structured calibration record attached to every v2 result."""
    calibrated = calibrate_score(raw_score, knots=knots, tail_slope=tail_slope)
    return {
        'raw': round(float(raw_score), 4),
        'calibrated': calibrated,
        'version': version,
    }


def fit_pava(
    xs: Sequence[float],
    ys: Sequence[float],
    sample_weights: Sequence[float] | None = None,
) -> list[tuple[float, float]]:
    """Pool Adjacent Violators Algorithm (non-decreasing isotonic regression).

    Returns sorted (x_center, y_iso) pairs for each pooled block.
    """
    if len(xs) != len(ys) or not xs:
        raise ValueError('xs and ys must be non-empty sequences of equal length')
    weights = [1.0] * len(xs) if sample_weights is None else [float(w) for w in sample_weights]
    order = sorted(range(len(xs)), key=lambda i: float(xs[i]))

    # Blocks store [sum_w, sum_wx, sum_wy, min_x, max_x]
    blocks: list[list[float]] = []
    for idx in order:
        w = max(1e-9, weights[idx])
        x = float(xs[idx])
        y = float(ys[idx])
        blocks.append([w, w * x, w * y, x, x])
        while len(blocks) >= 2:
            prev = blocks[-2]
            curr = blocks[-1]
            if (prev[2] / prev[0]) > (curr[2] / curr[0]):
                blocks[-2] = [
                    prev[0] + curr[0],
                    prev[1] + curr[1],
                    prev[2] + curr[2],
                    prev[3],
                    curr[4],
                ]
                blocks.pop()
            else:
                break

    return [(b[1] / b[0], b[2] / b[0]) for b in blocks]


def fit_monotone_knots(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    sample_weights: Sequence[float] | None = None,
    num_knots: int = 8,
    min_slope: float = 0.25,
    max_slope: float = 2.0,
) -> list[tuple[float, float]]:
    """Fit smooth, strictly monotone piecewise-linear calibration knots via PAVA.

    Projects PAVA isotonic estimates onto quantile anchor points and enforces
    strictly positive bounded slopes [min_slope, max_slope] so there are no flat
    plateaus and small raw deltas (e.g. language/visa flags) stay bounded.
    """
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError('Need at least 2 points to fit monotone knots')

    pava_pts = fit_pava(xs, ys, sample_weights=sample_weights)
    sorted_x = sorted(float(v) for v in xs)

    # Pick evenly spaced quantile anchors across observed raw scores.
    n_k = max(3, min(num_knots, len(sorted_x)))
    quantiles = [0.02 + (0.96 * i / (n_k - 1)) for i in range(n_k)]
    anchors: list[float] = []
    for q in quantiles:
        pos = q * (len(sorted_x) - 1)
        lo = int(pos)
        hi = min(len(sorted_x) - 1, lo + 1)
        val = sorted_x[lo] + (pos - lo) * (sorted_x[hi] - sorted_x[lo])
        if not anchors or val > anchors[-1] + 0.5:
            anchors.append(val)
    if len(anchors) < 2:
        anchors = [sorted_x[0], sorted_x[-1] + 1.0]

    # Evaluate PAVA curve at each anchor via linear interpolation of block centers.
    def _eval_pava(x: float) -> float:
        if x <= pava_pts[0][0]:
            return pava_pts[0][1]
        if x >= pava_pts[-1][0]:
            return pava_pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pava_pts, pava_pts[1:]):
            if x0 <= x <= x1:
                if x1 <= x0:
                    return y1
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return pava_pts[-1][1]

    knot_y = [max(2.0, min(98.0, _eval_pava(ax))) for ax in anchors]

    # Enforce slope bounds [min_slope, max_slope] via forward/backward projection.
    for _ in range(8):
        for i in range(1, len(anchors)):
            dx = anchors[i] - anchors[i - 1]
            lo = knot_y[i - 1] + min_slope * dx
            hi = knot_y[i - 1] + max_slope * dx
            if knot_y[i] < lo:
                knot_y[i] = lo
            elif knot_y[i] > hi:
                knot_y[i] = hi
        # Keep inside [1.0, 99.0] by shifting/clamping while preserving monotonicity.
        if knot_y[-1] > 99.0:
            excess = knot_y[-1] - 99.0
            knot_y = [max(1.0, y - excess) for y in knot_y]
        for i in range(len(anchors) - 2, -1, -1):
            dx = anchors[i + 1] - anchors[i]
            hi = knot_y[i + 1] - min_slope * dx
            lo = knot_y[i + 1] - max_slope * dx
            if knot_y[i] > hi:
                knot_y[i] = max(1.0, hi)
            elif knot_y[i] < lo:
                knot_y[i] = max(1.0, lo)

    return [(round(ax, 2), round(max(0.5, min(99.5, ay)), 2)) for ax, ay in zip(anchors, knot_y)]
