"""Shared loader for evaluation datasets (profiles/, jobs/, labels.json)."""
from __future__ import annotations

import json
from pathlib import Path

VALID_SPLITS = ("all", "dev", "heldout")


def _load_dir(directory: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows[str(data.get("id") or path.stem)] = data
    return rows


def load_profiles(profiles_dir: Path) -> dict[str, dict]:
    """Load candidate profiles keyed by profile_id."""
    return _load_dir(profiles_dir)


def load_jobs(jobs_dir: Path) -> dict[str, dict]:
    """Load job postings keyed by job_id."""
    return _load_dir(jobs_dir)


def load_labels(labels_path: Path) -> list[dict]:
    """Load ground-truth case labels from labels.json."""
    rows = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{labels_path} must contain a JSON list")
    return rows


def load_cases(
    *,
    split: str = "all",
    profiles_dir: Path,
    jobs_dir: Path,
    labels_path: Path,
) -> list[dict]:
    """Return evaluation cases compatible with ``tests.eval.run``.

    Each case holds id, profile_id, profile, job_id, job, grade, should_hide, rationale,
    tags, lang, split, pair_id (the job's translation-pair id, or None) and a nested ``label`` dict.
    """
    if split not in VALID_SPLITS:
        raise ValueError(f"Invalid split {split!r}; expected 'all', 'dev', or 'heldout'")
    profiles = load_profiles(profiles_dir)
    jobs = load_jobs(jobs_dir)
    cases: list[dict] = []
    for row in load_labels(labels_path):
        row_split = str(row["split"])
        if split != "all" and row_split != split:
            continue
        profile_id = str(row["profile_id"])
        job_id = str(row["job_id"])
        if profile_id not in profiles:
            raise KeyError(f"Label {row['case_id']!r} references unknown profile {profile_id!r}")
        if job_id not in jobs:
            raise KeyError(f"Label {row['case_id']!r} references unknown job {job_id!r}")
        grade = int(row["grade"])
        should_hide = bool(row["should_hide"])
        rationale = str(row.get("rationale") or "")
        cases.append(
            {
                "id": str(row["case_id"]),
                "profile_id": profile_id,
                "profile": profiles[profile_id],
                "job_id": job_id,
                "job": jobs[job_id],
                "grade": grade,
                "should_hide": should_hide,
                "rationale": rationale,
                "label": {"grade": grade, "should_hide": should_hide, "rationale": rationale},
                "tags": list(row.get("tags") or []),
                "lang": str(jobs[job_id]["lang"]),
                "split": row_split,
                "pair_id": jobs[job_id].get("pair_id") or None,
            }
        )
    return cases
