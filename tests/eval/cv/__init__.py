"""Private real-CV evaluation dataset loader for Radius job matcher.

The dataset (one person's anonymised CVs, real posting summaries and labels) is
private: it lives in the gitignored ``data/eval/cv`` directory, or wherever
``RADIUS_PRIVATE_EVAL_DIR`` points. Use :func:`is_available` before loading.
Provides the same ``load_cases()`` interface as :mod:`tests.eval.public`.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.eval import dataset

CV_EVAL_DIR = Path(os.environ.get("RADIUS_PRIVATE_EVAL_DIR") or
                   Path(__file__).resolve().parents[3] / "data" / "eval" / "cv")
PROFILES_DIR = CV_EVAL_DIR / "profiles"
JOBS_DIR = CV_EVAL_DIR / "jobs"
LABELS_PATH = CV_EVAL_DIR / "labels.json"
BASELINE_PATH = CV_EVAL_DIR / "baseline.json"


def is_available() -> bool:
    """True when the private dataset is present on this machine."""
    return PROFILES_DIR.is_dir() and JOBS_DIR.is_dir() and LABELS_PATH.is_file()


def load_profiles(profiles_dir: Path = PROFILES_DIR) -> dict[str, dict]:
    """Load anonymised candidate profiles keyed by profile_id."""
    return dataset.load_profiles(profiles_dir)


def load_jobs(jobs_dir: Path = JOBS_DIR) -> dict[str, dict]:
    """Load real public job postings keyed by job_id."""
    return dataset.load_jobs(jobs_dir)


def load_labels(labels_path: Path = LABELS_PATH) -> list[dict]:
    """Load ground-truth case labels from labels.json."""
    return dataset.load_labels(labels_path)


def load_cases(
    *,
    split: str = "all",
    profiles_dir: Path = PROFILES_DIR,
    jobs_dir: Path = JOBS_DIR,
    labels_path: Path = LABELS_PATH,
) -> list[dict]:
    """Return evaluation cases compatible with ``tests.eval.run``."""
    return dataset.load_cases(
        split=split, profiles_dir=profiles_dir, jobs_dir=jobs_dir, labels_path=labels_path
    )


__all__ = [
    "BASELINE_PATH",
    "CV_EVAL_DIR",
    "JOBS_DIR",
    "LABELS_PATH",
    "PROFILES_DIR",
    "is_available",
    "load_cases",
    "load_jobs",
    "load_labels",
    "load_profiles",
]
