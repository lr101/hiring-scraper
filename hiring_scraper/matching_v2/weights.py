"""Versioned feature weights, bounded penalty scales, and calibration knots for v2."""
from __future__ import annotations

VERSION = 'v2-linear-iso-2'

# Additive feature weights applied to features in [0.0, 1.0].
# Positive weights reward demonstrated job-profile alignment; domain_mismatch
# penalizes uncovered specialist professions/duties.
FEATURE_WEIGHTS: dict[str, float] = {
    'duty_coverage': 30.00,
    'requirement_coverage': 12.00,
    'skill_overlap': 20.50,
    'role_alignment': 20.00,
    'domain_alignment': 16.00,
    'seniority_fit': 11.50,
    'semantic_similarity': 6.00,
    'domain_mismatch': -21.50,
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
    (-19.73, 3.50),
    (2.53, 12.81),
    (21.47, 20.67),
    (38.10, 28.22),
    (51.88, 57.11),
    (65.41, 82.50),
    (74.71, 87.74),
    (91.40, 95.19),
)

# Extrapolation slope outside the outer knots (kept strictly positive so
# strongest attainable raw scores cleanly reach 100 and weakest reach 0).
CALIBRATION_TAIL_SLOPE: float = 0.92

# Fit-tier score thresholds (applied to calibrated 0-100 score + gates only).
TIER_RECOMMENDED_MIN: int = 65
TIER_POSSIBLE_MIN: int = 30
