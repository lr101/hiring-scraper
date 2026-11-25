"""Public composite evaluation dataset for the Radius job matcher.

Six fictional composite candidate profiles, original job summaries and graded
labels, all committed to the repository. See ``README.md`` and ``RUBRIC.md``.
Provides the same ``load_cases()`` interface as :mod:`tests.eval.cv`.
"""
from __future__ import annotations

from pathlib import Path

from tests.eval import dataset

PUBLIC_EVAL_DIR = Path(__file__).resolve().parent
PROFILES_DIR = PUBLIC_EVAL_DIR / "profiles"
JOBS_DIR = PUBLIC_EVAL_DIR / "jobs"
LABELS_PATH = PUBLIC_EVAL_DIR / "labels.json"
BASELINE_KEY_SUFFIX = "public"


def is_available() -> bool:
    return PROFILES_DIR.is_dir() and JOBS_DIR.is_dir() and LABELS_PATH.is_file()


def load_profiles(profiles_dir: Path = PROFILES_DIR) -> dict[str, dict]:
    """Load fictional composite profiles keyed by profile_id."""
    return dataset.load_profiles(profiles_dir)


def load_jobs(jobs_dir: Path = JOBS_DIR) -> dict[str, dict]:
    """Load job summaries keyed by job_id."""
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
    "JOBS_DIR",
    "LABELS_PATH",
    "PROFILES_DIR",
    "PUBLIC_EVAL_DIR",
    "is_available",
    "load_cases",
    "load_jobs",
    "load_labels",
    "load_profiles",
]
