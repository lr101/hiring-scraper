"""Versioned v3 weights, support transform and calibration knots.

Fitted on the DEV split of the public evaluation set from recorded model outputs
(``python -m tests.eval.fit_v3``). The low end is a fixed ramp so jobs with little
evidence spread over 0-29 instead of sharing one score; knots above raw 25 were
searched against the score-range gates (strong >= 80, irrelevant < 30, no plateau).
"""
from __future__ import annotations

VERSION = 'v3-evidence-1'

# Share of the raw 0-100 score per component; missing components (for example a
# posting without listed duties) are renormalised away.
COMPONENT_WEIGHTS: dict[str, float] = {
    'duties': 0.32,
    'must': 0.28,
    'nice': 0.06,
    'role': 0.17,
    'domain': 0.17,
}

# Reranker probability -> support in [0, 1], linear in log-odds between LOW and HIGH.
SUPPORT_LOW = 0.02
SUPPORT_HIGH = 0.995
# Few-item shrinkage: mean = (sum + PRIOR_WEIGHT * PRIOR) / (n + PRIOR_WEIGHT).
PRIOR_WEIGHT = 0.5
PRIOR = 0.25

DIRECT_SUPPORT = 0.65
PARTIAL_SUPPORT = 0.3

SOFT_PENALTIES: dict[str, float] = {
    'work_style_mismatch': 4.0,
    'employment_type_mismatch': 8.0,
    'formal_prerequisite_max': 6.0,
    'language_visa_flag_max': 2.0,
    'total_soft_penalty_cap': 20.0,
}

CALIBRATION_KNOTS: tuple[tuple[float, float], ...] = (
    (-8.0, 0.0),
    (-2.0, 4.0),
    (0.0, 10.0),
    (1.0, 14.0),
    (2.0, 17.0),
    (3.0, 20.0),
    (5.0, 23.0),
    (7.5, 27.0),
    (25.0, 39.22),
    (50.0, 56.84),
    (60.0, 60.0),
    (70.0, 64.03),
    (80.0, 70.39),
    (86.0, 79.54),
    (90.0, 81.37),
    (95.0, 87.57),
    (100.0, 100.0),
)
CALIBRATION_TAIL_SLOPE = 0.5

TIER_RECOMMENDED_MIN = 65
TIER_POSSIBLE_MIN = 30
