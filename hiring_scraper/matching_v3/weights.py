"""Versioned v3 weights, support transform and calibration knots.

Fitted on the DEV split of the public evaluation set from recorded model outputs;
``python -m tests.eval.fit_v3 --keep-weights`` reproduces these knots exactly. The
low end is a fixed ramp (``fit_v3.LOW_RAMP``) so jobs with little evidence spread
over 0-27; above it, isotonic regression of grade-band targets is sampled at raw
quantiles, and a small grid (knot count, straight-line blend, strong-case margin) is
chosen by the target acceptance gates on DEV. HELDOUT is never used for fitting.
"""
from __future__ import annotations

VERSION = 'v3-evidence-4'

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

# Role fit counts fully only for a role the person is looking for. A past job title
# still shows relevant experience, but less so; a past title the person excluded
# (for example "Head of") says little about the roles they want next.
ROLE_SOURCE_FACTORS: dict[str, float] = {
    'desired': 1.0,
    'secondary': 0.85,
    'past': 0.7,
    'past_excluded': 0.4,
}

# Raw points per career-level step between the posting and the profile's highest
# level (student 0, junior 1, unstated/mid 2, senior 3, lead/staff 4, head/director 5,
# VP/C-level 6). Junior -> senior costs little; leadership levels cost more.
SENIORITY_STEP_UP: tuple[float, ...] = (0.0, 0.0, 2.0, 4.0, 5.0, 5.0)  # step i: level i -> i + 1
SENIORITY_STEP_DOWN = 3.0

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
    (16.32, 39.7),
    (32.54, 46.27),
    (58.35, 63.84),
    (70.0, 68.71),
    (78.55, 71.91),
    (86.1, 78.23),
    (89.95, 83.0),
    (91.63, 85.08),
    (98.39, 96.17),
    (100.0, 100.0),
)
CALIBRATION_TAIL_SLOPE = 0.5
# Evidence rated by GPT-6 Luna (ChatGPT subscription) has a different support distribution
# than the local reranker; same weights, own monotone calibration fitted on the DEV split of
# the public evaluation set (``tests.eval.fit_v3 --provider luna --keep-weights``).
# Below raw 39.58 the Luna knots are a straight-line ramp, not the shared LOW_RAMP: Luna rates
# unrelated work around raw 8-20, which the shared ramp bunched at 27-29 and let reach 30-33
# in production. On DEV, irrelevant postings reach raw 20.0, so 30 sits at raw 21.
LUNA_CALIBRATION_KNOTS: tuple[tuple[float, float], ...] = (
    (-8.0, 0.0), (-2.0, 3.0), (0.0, 5.0), (5.0, 10.0), (10.0, 16.0), (15.0, 22.0), (21.0, 30.0),
    (30.0, 45.0), (39.58, 60.97), (53.97, 68.17), (55.74, 69.62), (59.09, 76.62),
    (63.8, 86.45), (70.16, 90.97), (78.64, 96.51), (100.0, 100.0),
)

TIER_RECOMMENDED_MIN = 65
TIER_POSSIBLE_MIN = 30
