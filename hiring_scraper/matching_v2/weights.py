"""Versioned feature weights, bounded penalty scales, and calibration knots for v2."""
from __future__ import annotations

VERSION = 'v2-linear-iso-3'

# Additive feature weights applied to features in [0.0, 1.0].
# Positive weights reward demonstrated job-profile alignment; domain_mismatch
# penalizes uncovered specialist professions/duties.
FEATURE_WEIGHTS: dict[str, float] = {
    'duty_coverage': 30.00,
    'requirement_coverage': 14.00,
    'skill_overlap': 25.25,
    'role_alignment': 14.00,
    'domain_alignment': 14.75,
    'seniority_fit': 10.00,
    'semantic_similarity': 10.00,
    'domain_mismatch': -25.50,
}

# Bounded soft-constraint penalty magnitudes (deducted from raw_score).
# Every unique requirement clause is counted at most once.
# language_visa_flag is strictly bounded <= 5.0 (kept <= 2.0 raw so calibrated
# delta stays well below 5 points and never jumps > 1 grade band).
SOFT_PENALTIES: dict[str, float] = {
    'tenure_shortfall_max': 10.0,
    'seniority_gap': 8.0,
    'work_style_mismatch': 4.0,
    'employment_type_mismatch': 8.0,
    'formal_prerequisite_max': 8.0,
    'language_visa_flag_max': 2.0,
    'total_soft_penalty_cap': 28.0,
}

# Strictly monotone piecewise-linear calibration knots: (raw_score, calibrated_score).
# Fitted on DEV split only by `python -m hiring_scraper.matching_v2.fit`.
CALIBRATION_KNOTS: tuple[tuple[float, float], ...] = (
    (-23.1500, 2.5000),
    (-4.2600, 10.2440),
    (9.5800, 15.7840),
    (25.9400, 22.4239),
    (36.7400, 27.1372),
    (39.8627, 28.7500),
    (47.6900, 46.6213),
    (58.7400, 68.1743),
    (65.1665, 79.7500),
    (66.6500, 80.6588),
    (75.4700, 85.5484),
    (88.2100, 91.2500),
)

# Extrapolation slope outside the outer knots (kept strictly positive so
# strongest attainable raw scores cleanly reach 100 and weakest reach 0).
CALIBRATION_TAIL_SLOPE: float = 0.92

# Fit-tier score thresholds (applied to calibrated 0-100 score + gates only).
TIER_RECOMMENDED_MIN: int = 65
TIER_POSSIBLE_MIN: int = 30
