"""Pure evaluation metrics for offline job-matching benchmarks.

Calibration mapping (grade buckets -> 0-100 score bands -> [0, 1] targets):
- Grade 0 (irrelevant / incompatible): band  0-29 -> target 0.15 (band midpoint)
- Grade 1 (adjacent / partial lead):   band 30-59 -> target 0.45 (band midpoint)
- Grade 2 (good fit):                  band 60-79 -> target 0.70 (band midpoint)
- Grade 3 (strong fit):                band 80-100 -> target 0.90 (band midpoint)

Expected Calibration Error (ECE) groups cases by ground-truth grade bucket,
compares each bucket's mean normalized effective score (score / 100, or 0.0
when hidden) to the bucket's target, and weights each bucket's absolute error
by its share of cases. This keeps ECE in [0, 1] and prevents a constant scorer
from achieving zero error.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence

GRADE_SCORE_BANDS: dict[int, tuple[int, int]] = {
    0: (0, 29),
    1: (30, 59),
    2: (60, 79),
    3: (80, 100),
}

GRADE_SCORE_TARGETS: dict[int, float] = {
    0: 0.15,
    1: 0.45,
    2: 0.70,
    3: 0.90,
}

DEFAULT_PLATEAUS: tuple[int, ...] = (29, 59, 85)


def effective_ranking_score(score: float, is_hidden: bool) -> float:
    """Return effective ranking score where hidden (ineligible) cases sort last."""
    return -1.0 if is_hidden else float(score)


def rank_indices(
    scores: Sequence[float],
    hidden: Sequence[bool],
    case_ids: Sequence[str] | None = None,
) -> list[int]:
    """Return item indices ordered by effective rank descending, ties broken deterministically."""
    if len(scores) != len(hidden):
        raise ValueError("scores and hidden must have the same length")
    if case_ids is not None and len(case_ids) != len(scores):
        raise ValueError("case_ids must match scores length")

    def _tie_key(idx: int) -> str:
        raw = case_ids[idx] if case_ids is not None else str(idx)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return sorted(
        range(len(scores)),
        key=lambda idx: (-effective_ranking_score(scores[idx], hidden[idx]), _tie_key(idx)),
    )


def ndcg_at_k(
    grades_in_ranked_order: Sequence[int | float],
    k: int = 10,
    *,
    hidden_in_ranked_order: Sequence[bool] | None = None,
) -> float:
    """Compute normalized Discounted Cumulative Gain at rank k.

    Uses exponential relevance gain (2**grade - 1) / log2(rank + 1) with
    1-based rank. When ``hidden_in_ranked_order`` is provided, hidden jobs
    receive zero gain in the observed ranking while retaining their true
    grades in the ideal ranking. Returns 0.0 if k <= 0 or no positive grades exist.
    """
    if k <= 0 or not grades_in_ranked_order:
        return 0.0
    if hidden_in_ranked_order is not None and len(hidden_in_ranked_order) != len(grades_in_ranked_order):
        raise ValueError("hidden_in_ranked_order must match grades_in_ranked_order length")

    def _dcg(grades: Sequence[int | float]) -> float:
        total = 0.0
        for idx, grade in enumerate(grades[:k]):
            if grade > 0:
                total += ((2.0 ** float(grade)) - 1.0) / math.log2(idx + 2.0)
        return total

    ideal = _dcg(sorted(grades_in_ranked_order, reverse=True))
    if ideal <= 0.0:
        return 0.0
    if hidden_in_ranked_order is None:
        observed_grades = grades_in_ranked_order
    else:
        observed_grades = [
            0 if is_hidden else grade
            for grade, is_hidden in zip(grades_in_ranked_order, hidden_in_ranked_order)
        ]
    return _dcg(observed_grades) / ideal


def precision_at_k(
    grades_in_ranked_order: Sequence[int | float],
    k: int = 10,
    relevant_grade: int = 2,
    *,
    hidden_in_ranked_order: Sequence[bool] | None = None,
) -> float:
    """Fraction of top-k ranked items that are visible and have grade >= relevant_grade."""
    if k <= 0 or not grades_in_ranked_order:
        return 0.0
    if hidden_in_ranked_order is not None and len(hidden_in_ranked_order) != len(grades_in_ranked_order):
        raise ValueError("hidden_in_ranked_order must match grades_in_ranked_order length")
    window_grades = grades_in_ranked_order[:k]
    window_hidden = (
        [False] * len(window_grades)
        if hidden_in_ranked_order is None
        else hidden_in_ranked_order[:k]
    )
    hits = sum(
        1
        for grade, is_hidden in zip(window_grades, window_hidden)
        if (not is_hidden) and grade >= relevant_grade
    )
    return hits / len(window_grades)


def hide_precision(should_hide: Sequence[bool], hidden: Sequence[bool]) -> float:
    """Precision of hiding decisions (a case is hidden when eligible is False).

    Returns 1.0 when nothing is hidden (zero false hides).
    """
    if len(should_hide) != len(hidden):
        raise ValueError("should_hide and hidden must have the same length")
    predicted_hidden = sum(1 for is_hidden in hidden if is_hidden)
    if predicted_hidden == 0:
        return 1.0
    true_positives = sum(1 for want, is_hidden in zip(should_hide, hidden) if want and is_hidden)
    return true_positives / predicted_hidden


def hide_recall(should_hide: Sequence[bool], hidden: Sequence[bool]) -> float:
    """Recall of hiding decisions over cases labeled should_hide=True.

    Returns 1.0 when no case is labeled should_hide=True.
    """
    if len(should_hide) != len(hidden):
        raise ValueError("should_hide and hidden must have the same length")
    actual_should_hide = sum(1 for want in should_hide if want)
    if actual_should_hide == 0:
        return 1.0
    true_positives = sum(1 for want, is_hidden in zip(should_hide, hidden) if want and is_hidden)
    return true_positives / actual_should_hide


def good_hidden_rate(
    grades: Sequence[int | float],
    hidden: Sequence[bool],
    good_grade: int = 2,
) -> float:
    """Fraction of good/strong cases (grade >= good_grade) that were hidden."""
    if len(grades) != len(hidden):
        raise ValueError("grades and hidden must have the same length")
    good_total = sum(1 for grade in grades if grade >= good_grade)
    if good_total == 0:
        return 0.0
    good_hidden = sum(1 for grade, is_hidden in zip(grades, hidden) if grade >= good_grade and is_hidden)
    return good_hidden / good_total


def de_en_parity(pairs: Sequence[tuple[float, float]]) -> float:
    """Mean absolute score difference |score_de - score_en| across translation pairs."""
    if not pairs:
        return 0.0
    return sum(abs(float(score_de) - float(score_en)) for score_de, score_en in pairs) / len(pairs)


def pair_eligibility_disagreement(pairs: Sequence[tuple[bool, bool]]) -> float:
    """Fraction of translation pairs where DE and EN eligibility decisions differ."""
    if not pairs:
        return 0.0
    disagreements = sum(1 for elig_de, elig_en in pairs if bool(elig_de) != bool(elig_en))
    return disagreements / len(pairs)


def pairwise_auc(grades: Sequence[int | float], scores: Sequence[float]) -> float:
    """Pairwise ranking AUC: P(score_i > score_j | grade_i > grade_j), ties = 0.5.

    Returns 0.5 when no comparable pairs exist.
    """
    if len(grades) != len(scores):
        raise ValueError("grades and scores must have the same length")
    n = len(grades)
    comparable = 0
    wins = 0.0
    for i in range(n):
        gi = grades[i]
        si = float(scores[i])
        for j in range(n):
            if gi > grades[j]:
                comparable += 1
                sj = float(scores[j])
                if si > sj:
                    wins += 1.0
                elif si == sj:
                    wins += 0.5
    if comparable == 0:
        return 0.5
    return wins / comparable


def ranking_relevance(grades: Sequence[int | float], should_hide: Sequence[bool]) -> list[int | float]:
    """Ranking relevance per case: ``should_hide`` cases carry no gain (0), others their grade.

    Used for both the observed and the ideal ranking, so correctly hiding a case
    is never punished as lost gain. Hide quality is measured by hide precision/recall.
    """
    if len(grades) != len(should_hide):
        raise ValueError("grades and should_hide must have the same length")
    return [0 if hide else grade for grade, hide in zip(grades, should_hide)]


def ranking_auc(
    grades: Sequence[int | float],
    scores: Sequence[float],
    should_hide: Sequence[bool],
) -> float:
    """Pairwise AUC over cases that should stay visible (``should_hide`` cases are excluded)."""
    if not (len(grades) == len(scores) == len(should_hide)):
        raise ValueError("grades, scores and should_hide must have the same length")
    kept = [idx for idx, hide in enumerate(should_hide) if not hide]
    return pairwise_auc([grades[idx] for idx in kept], [scores[idx] for idx in kept])


def band_error(
    grades: Sequence[int],
    scores: Sequence[float],
    grade_bands: Mapping[int, tuple[int, int]] | None = None,
    *,
    is_hidden: Sequence[bool] | None = None,
) -> float:
    """Mean normalized distance from each visible case's score to its grade band.

    For each visible case (not hidden), the distance to its grade band
    ``[low, high]`` is 0 if ``low <= score <= high``, ``low - score`` if below,
    or ``score - high`` if above. The mean distance across visible cases is
    normalized by 100 into ``[0, 1]``. Returns 0.0 when no visible cases exist.
    """
    if len(grades) != len(scores):
        raise ValueError("grades and scores must have the same length")
    if is_hidden is not None and len(is_hidden) != len(grades):
        raise ValueError("is_hidden and grades must have the same length")
    bands = GRADE_SCORE_BANDS if grade_bands is None else grade_bands
    hidden_flags = [False] * len(grades) if is_hidden is None else list(is_hidden)
    total_distance = 0.0
    visible_count = 0
    for grade, score, hidden in zip(grades, scores, hidden_flags):
        if grade not in bands:
            raise ValueError(f"Unknown grade {grade!r}; expected one of {sorted(bands)}")
        if hidden:
            continue
        low, high = bands[grade]
        s = float(score)
        if s < low:
            total_distance += low - s
        elif s > high:
            total_distance += s - high
        visible_count += 1
    if visible_count == 0:
        return 0.0
    return (total_distance / visible_count) / 100.0


def expected_calibration_error(
    grades: Sequence[int],
    scores: Sequence[float],
    grade_targets: Mapping[int, float] | None = None,
    *,
    is_hidden: Sequence[bool] | None = None,
) -> float:
    """Expected calibration error over grade buckets mapped to score bands.

    Each ground-truth grade bucket g is mapped to its target normalized score
    in [0, 1] via ``grade_targets`` (defaults to ``GRADE_SCORE_TARGETS``:
    0 -> 0.15 [0-29], 1 -> 0.45 [30-59], 2 -> 0.70 [60-79], 3 -> 0.90 [80-100]).
    When ``is_hidden`` is provided, hidden cases contribute 0.0 so hiding a
    high-grade job degrades calibration.
    Returns weighted average |mean(effective_score / 100) - target_g| across buckets.
    """
    if len(grades) != len(scores):
        raise ValueError("grades and scores must have the same length")
    if is_hidden is not None and len(is_hidden) != len(grades):
        raise ValueError("is_hidden and grades must have the same length")
    total = len(grades)
    if total == 0:
        return 0.0
    targets = GRADE_SCORE_TARGETS if grade_targets is None else grade_targets
    hidden_flags = [False] * total if is_hidden is None else list(is_hidden)
    buckets: dict[int, list[float]] = {}
    for grade, score, hidden in zip(grades, scores, hidden_flags):
        if grade not in targets:
            raise ValueError(f"Unknown grade {grade!r}; expected one of {sorted(targets)}")
        effective = 0.0 if hidden else max(0.0, min(100.0, float(score))) / 100.0
        buckets.setdefault(grade, []).append(effective)
    ece = 0.0
    for grade, values in buckets.items():
        mean_pred = sum(values) / len(values)
        ece += (len(values) / total) * abs(mean_pred - targets[grade])
    return ece


def score_spread(
    scores: Sequence[float],
    plateaus: Sequence[int] = DEFAULT_PLATEAUS,
) -> dict[str, object]:
    """Summarize score dispersion and plateau bunching (default plateaus: 29, 59, 85)."""
    plateau_keys = tuple(int(p) for p in plateaus)
    if not scores:
        return {
            "std": 0.0,
            "distinct_count": 0,
            "plateau_fraction": 0.0,
            "plateau_counts": {str(p): 0 for p in plateau_keys},
        }
    floats = [float(s) for s in scores]
    mean = sum(floats) / len(floats)
    variance = sum((x - mean) ** 2 for x in floats) / len(floats)
    std = math.sqrt(variance)
    rounded = [round(x) for x in floats]
    plateau_counts = {str(p): sum(1 for r in rounded if r == p) for p in plateau_keys}
    on_plateau = sum(plateau_counts.values())
    return {
        "std": std,
        "distinct_count": len(set(floats)),
        "plateau_fraction": on_plateau / len(floats),
        "plateau_counts": plateau_counts,
    }
