"""Validate the public composite evaluation dataset.

Usage:
    python -m tests.eval.public.check

Checks schema, counts, splits (stable group hash, no cross-split near-duplicates), the label
policy, that no personal contact details or URLs are present, and, when the private real-CV set
exists on this machine, that no public profile shares >= 4 calendar years or any 6-word run with
a private profile. Only pass/fail and counts are printed for the private comparison, never text.
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict

from tests.eval import cv as private_dataset
from tests.eval.cv.check import _expected_split, group_near_duplicate_jobs
from tests.eval.public import JOBS_DIR, LABELS_PATH, PROFILES_DIR, PUBLIC_EVAL_DIR, load_jobs, load_labels, load_profiles

MIN_CASES = 180
MIN_PROFILES = 6
MIN_CASES_PER_PROFILE = 25
MIN_SHOULD_HIDE = 15
MIN_TRANSLATION_PAIRS = 15
MIN_HIDE_CASES_PER_CATEGORY = 3
MAX_SHARED_YEARS = 3
SHARED_RUN_WORDS = 6
JOB_SHARED_RUN_WORDS = 8

REQUIRED_PROFILE_KEYS = (
    "id", "archetype", "location_region", "desired_roles", "secondary_roles", "skills", "languages",
    "language_levels", "experience_years", "work_styles", "employment_types", "summary",
    "application_details", "cv_career_facts", "skill_evidence", "application_preferences",
    "education", "certifications", "excluded_terms",
)
REQUIRED_JOB_KEYS = ("id", "lang", "title", "company", "location_text", "description", "description_kind")
REQUIRED_LABEL_KEYS = ("case_id", "profile_id", "job_id", "grade", "should_hide", "tags", "split", "rationale")
HARD_HIDE_TAGS = ("hard_hide_cefr", "hard_hide_excluded_term", "hard_hide_declined_onsite", "hard_hide_work_permit")
REQUIRED_ADVERSARIAL_TAGS = (
    "keyword_trap", "company_boast", "or_alternative", "qualitative_language", "seniority_gap",
    "metadata_only_onsite", "sponsorship_alternative", "long_clause",
)
NEVER_HIDDEN_TAGS = ("qualitative_language", "seniority_gap", "metadata_only_onsite", "sponsorship_alternative")
FORBIDDEN_TAGS = ("target_role", "applied", "app", "variant_eval")

PII_PATTERNS = (
    ("at-sign", re.compile(r"@")),
    ("URL", re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|de|org|net|io|ch|at)\b", re.I)),
    ("phone-like digits", re.compile(r"\+\d{2}|\b0\d{2,4}[\s/.-]\d{4,}|\d{7,}")),
    ("social profile", re.compile(r"linkedin|xing\.com|github\.com|twitter|instagram|facebook", re.I)),
)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
WORD_RE = re.compile(r"[a-z0-9äöüß]+")


def _iter_strings(obj: object, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}]")


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _runs(texts: list[str], size: int) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for text in texts:
        words = _words(text)
        found.update(tuple(words[i : i + size]) for i in range(len(words) - size + 1))
    return found


def _profile_years(profile: dict) -> set[str]:
    years: set[str] = set()
    facts = profile.get("cv_career_facts") or {}
    for kind in ("experiences", "education"):
        for entry in facts.get(kind) or []:
            for key in ("start", "end"):
                years.update(YEAR_RE.findall(str(entry.get(key) or "")))
    return years


def _profile_texts(profile: dict) -> list[str]:
    return [text for _path, text in _iter_strings(profile)]


def compare_with_private(public_profiles: dict[str, dict], public_jobs: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """Compare against the private set when present; returns (summary counts, errors)."""
    if not private_dataset.is_available():
        return None, []
    errors: list[str] = []
    private_profiles = private_dataset.load_profiles()
    private_jobs = private_dataset.load_jobs()
    max_years = 0
    shared_year_pairs = 0
    shared_run_total = 0
    for pub_id, pub in public_profiles.items():
        pub_years = _profile_years(pub)
        pub_runs = _runs(_profile_texts(pub), SHARED_RUN_WORDS)
        for priv in private_profiles.values():
            shared = len(pub_years & _profile_years(priv))
            max_years = max(max_years, shared)
            if shared > MAX_SHARED_YEARS:
                shared_year_pairs += 1
                errors.append(f"public profile {pub_id!r} shares {shared} calendar years with a private profile")
            runs = len(pub_runs & _runs(_profile_texts(priv), SHARED_RUN_WORDS))
            shared_run_total += runs
            if runs:
                errors.append(f"public profile {pub_id!r} shares {runs} six-word run(s) with a private profile")
    private_job_runs = _runs([f"{j.get('title', '')} {j.get('description', '')}" for j in private_jobs.values()], JOB_SHARED_RUN_WORDS)
    job_run_hits = 0
    for job_id, job in public_jobs.items():
        hits = len(_runs([f"{job.get('title', '')} {job.get('description', '')}"], JOB_SHARED_RUN_WORDS) & private_job_runs)
        job_run_hits += hits
        if hits:
            errors.append(f"public job {job_id!r} shares {hits} eight-word run(s) with a private job")
    summary = {
        "private_profiles": len(private_profiles),
        "private_jobs": len(private_jobs),
        "max_shared_years": max_years,
        "profile_pairs_over_year_limit": shared_year_pairs,
        "shared_six_word_runs": shared_run_total,
        "shared_eight_word_job_runs": job_run_hits,
    }
    return summary, errors


def validate_translation_pairs(jobs: dict[str, dict], labels: list[dict]) -> list[str]:
    """Each ``pair_id`` names exactly one DE job and one EN job (a faithful translation).

    For every profile that labels one member, the other member must carry the same
    grade, should_hide and tags, and both must sit in the same split.
    """
    errors: list[str] = []
    members: dict[str, list[str]] = defaultdict(list)
    for jid, job in jobs.items():
        pair_id = job.get("pair_id")
        if pair_id is None:
            continue
        if not isinstance(pair_id, str) or not re.fullmatch(r"pair_\d{2,}", pair_id):
            errors.append(f"Job {jid!r} has invalid pair_id {pair_id!r}")
            continue
        members[pair_id].append(jid)
    if len(members) < MIN_TRANSLATION_PAIRS:
        errors.append(f"Expected >= {MIN_TRANSLATION_PAIRS} translation pairs, found {len(members)}")
    by_pair_profile: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for row in labels:
        pair_id = jobs.get(row.get("job_id"), {}).get("pair_id")
        if pair_id:
            by_pair_profile[(pair_id, str(row.get("profile_id")))][str(row["job_id"])] = row
    for pair_id, jids in sorted(members.items()):
        if sorted(jobs[jid].get("lang") for jid in jids) != ["de", "en"]:
            errors.append(f"Pair {pair_id!r} must hold exactly one de and one en job, found {sorted(jids)}")
            continue
        for (pid, profile_id), rows in sorted(by_pair_profile.items()):
            if pid != pair_id:
                continue
            if set(rows) != set(jids):
                errors.append(f"Pair {pair_id!r} profile {profile_id!r} must label both members")
                continue
            first, second = (rows[jid] for jid in sorted(jids))
            for key in ("grade", "should_hide", "split"):
                if first.get(key) != second.get(key):
                    errors.append(f"Pair {pair_id!r} profile {profile_id!r} differs in {key}")
            if sorted(first.get("tags") or []) != sorted(second.get("tags") or []):
                errors.append(f"Pair {pair_id!r} profile {profile_id!r} differs in tags")
    return errors


def validate_dataset(*, compare_private: bool = True) -> tuple[dict[str, dict], dict[str, dict], list[dict], list[str]]:
    errors: list[str] = []
    for name in ("README.md", "RUBRIC.md"):
        if not (PUBLIC_EVAL_DIR / name).is_file():
            errors.append(f"Missing {name}")

    profiles = load_profiles(PROFILES_DIR)
    jobs = load_jobs(JOBS_DIR)
    labels = load_labels(LABELS_PATH)

    if len(profiles) < MIN_PROFILES:
        errors.append(f"Expected >= {MIN_PROFILES} profiles, found {len(profiles)}")
    for pid, prof in profiles.items():
        for key in REQUIRED_PROFILE_KEYS:
            if key not in prof:
                errors.append(f"Profile {pid!r} missing key {key!r}")
        if prof.get("id") != pid:
            errors.append(f"Profile file {pid!r} has mismatched id {prof.get('id')!r}")
        for path, text in _iter_strings(prof):
            for label, pattern in PII_PATTERNS:
                if pattern.search(text):
                    errors.append(f"Profile {pid!r}.{path} contains {label}")

    id_re = re.compile(r"^job_\d{4}$")
    for jid, job in jobs.items():
        if not id_re.match(jid):
            errors.append(f"Job {jid!r} must use a neutral id job_NNNN")
        for key in REQUIRED_JOB_KEYS:
            if not isinstance(job.get(key), str) or not job[key].strip():
                errors.append(f"Job {jid!r} missing/empty string key {key!r}")
        if job.get("id") != jid:
            errors.append(f"Job file {jid!r} has mismatched id {job.get('id')!r}")
        if job.get("lang") not in ("de", "en"):
            errors.append(f"Job {jid!r} has invalid lang {job.get('lang')!r}")
        if job.get("description_kind") != "summary":
            errors.append(f"Job {jid!r} must have description_kind='summary'")
        wa = job.get("work_arrangement")
        if wa is not None and wa not in ("remote", "hybrid", "onsite"):
            errors.append(f"Job {jid!r} has invalid work_arrangement {wa!r}")
        desc = job.get("description", "")
        if not 100 <= len(desc) <= 2500:
            errors.append(f"Job {jid!r} description length {len(desc)} outside 100-2500")
        for key in ("title", "company", "location_text", "description"):
            for label, pattern in PII_PATTERNS:
                if pattern.search(str(job.get(key, ""))):
                    errors.append(f"Job {jid!r}.{key} contains {label}")

    group_keys, near_pairs = group_near_duplicate_jobs(jobs)

    seen: set[str] = set()
    job_splits: dict[str, set[str]] = defaultdict(set)
    by_profile: dict[str, list[dict]] = defaultdict(list)
    tag_counts: Counter[str] = Counter()
    for index, row in enumerate(labels):
        for key in REQUIRED_LABEL_KEYS:
            if key not in row:
                errors.append(f"Label row {index} missing key {key!r}")
        cid = str(row.get("case_id"))
        if cid in seen:
            errors.append(f"Duplicate case_id {cid!r}")
        seen.add(cid)
        pid, jid = row.get("profile_id"), row.get("job_id")
        if pid not in profiles:
            errors.append(f"Label {cid!r} references unknown profile {pid!r}")
        if jid not in jobs:
            errors.append(f"Label {cid!r} references unknown job {jid!r}")
        if cid != f"{pid}__{jid}":
            errors.append(f"Label {cid!r} case_id must be '<profile_id>__<job_id>'")
        grade, hide, tags = row.get("grade"), row.get("should_hide"), row.get("tags")
        if type(grade) is not int or grade not in (0, 1, 2, 3):
            errors.append(f"Label {cid!r} has invalid grade {grade!r}")
        if type(hide) is not bool:
            errors.append(f"Label {cid!r} has non-bool should_hide {hide!r}")
        elif hide and isinstance(grade, int) and grade >= 2:
            errors.append(f"Label {cid!r} has should_hide=True with grade {grade}")
        if not isinstance(tags, list) or not tags:
            errors.append(f"Label {cid!r} needs a non-empty tags list")
            tags = []
        for tag in tags:
            tag_counts[tag] += 1
            if tag in FORBIDDEN_TAGS:
                errors.append(f"Label {cid!r} uses forbidden tag {tag!r}")
            if tag in HARD_HIDE_TAGS and hide is not True:
                errors.append(f"Label {cid!r} tagged {tag!r} must have should_hide=True")
            if tag in NEVER_HIDDEN_TAGS and hide is not False:
                errors.append(f"Label {cid!r} tagged {tag!r} must have should_hide=False")
        if hide is True and not any(tag in HARD_HIDE_TAGS for tag in tags):
            errors.append(f"Label {cid!r} should_hide=True needs one hard_hide_* tag")
        rationale = row.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"Label {cid!r} needs a rationale")
        else:
            for label, pattern in PII_PATTERNS:
                if pattern.search(rationale):
                    errors.append(f"Label {cid!r}.rationale contains {label}")
        split = row.get("split")
        if split not in ("dev", "heldout"):
            errors.append(f"Label {cid!r} has invalid split {split!r}")
        elif jid in group_keys:
            job_splits[jid].add(split)
            expected = _expected_split(group_keys[jid])
            if split != expected:
                errors.append(f"Label {cid!r} split {split!r} != stable group hash {expected!r}")
        by_profile[str(pid)].append(row)

    for jid, splits in job_splits.items():
        if len(splits) > 1:
            errors.append(f"Job {jid!r} appears in both splits")
    for a, b, jaccard in near_pairs:
        if job_splits.get(a) != job_splits.get(b):
            errors.append(f"Cross-split near-duplicate jobs {a!r}/{b!r} (Jaccard {jaccard:.2f})")

    errors.extend(validate_translation_pairs(jobs, labels))

    if len(labels) < MIN_CASES:
        errors.append(f"Expected >= {MIN_CASES} cases, found {len(labels)}")
    unused = sorted(set(jobs) - {str(r.get("job_id")) for r in labels})
    if unused:
        errors.append(f"{len(unused)} jobs have no label: {unused[:5]}")
    for pid in profiles:
        rows = by_profile.get(pid, [])
        if len(rows) < MIN_CASES_PER_PROFILE:
            errors.append(f"Profile {pid!r} has only {len(rows)} cases (< {MIN_CASES_PER_PROFILE})")
        if {r.get("grade") for r in rows} != {0, 1, 2, 3}:
            errors.append(f"Profile {pid!r} must cover grades 0-3")
        if {r.get("split") for r in rows} != {"dev", "heldout"}:
            errors.append(f"Profile {pid!r} must have dev and heldout cases")
    langs = Counter(jobs[r["job_id"]]["lang"] for r in labels if r.get("job_id") in jobs)
    if labels and not 0.40 <= langs["de"] / len(labels) <= 0.60:
        errors.append(f"German share {langs['de'] / len(labels):.2f} outside 0.40-0.60")
    held = sum(1 for r in labels if r.get("split") == "heldout")
    if labels and not 0.20 <= held / len(labels) <= 0.40:
        errors.append(f"Heldout share {held / len(labels):.2f} outside 0.20-0.40")
    if sum(1 for r in labels if r.get("should_hide") is True) < MIN_SHOULD_HIDE:
        errors.append(f"Expected >= {MIN_SHOULD_HIDE} should_hide cases")
    for tag in HARD_HIDE_TAGS:
        if tag_counts[tag] < MIN_HIDE_CASES_PER_CATEGORY:
            errors.append(f"Hide category {tag!r} has {tag_counts[tag]} cases (< {MIN_HIDE_CASES_PER_CATEGORY})")
    for tag in REQUIRED_ADVERSARIAL_TAGS:
        if tag_counts[tag] < 1:
            errors.append(f"Missing adversarial tag {tag!r}")

    if compare_private:
        _summary, private_errors = compare_with_private(profiles, jobs)
        errors.extend(private_errors)
    return profiles, jobs, labels, errors


def main() -> int:
    profiles, jobs, labels, errors = validate_dataset()
    print("=== PUBLIC EVAL DATASET VALIDATION ===")
    if errors:
        print(f"FAILED with {len(errors)} error(s):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASSED: schema, counts, label policy, stable splits, no contact details/URLs (0 errors).")
    langs = Counter(jobs[r["job_id"]]["lang"] for r in labels)
    print(f"Profiles {len(profiles)} | jobs {len(jobs)} | cases {len(labels)} | DE/EN {langs['de']}/{langs['en']}")
    pairs = {job["pair_id"] for job in jobs.values() if job.get("pair_id")}
    print(f"Translation pairs {len(pairs)}")
    print(f"Splits {dict(Counter(r['split'] for r in labels))} | should_hide {sum(r['should_hide'] for r in labels)}")
    summary, _ = compare_with_private(profiles, jobs)
    if summary is None:
        print("Private overlap check: SKIPPED (private dataset not present on this machine).")
    else:
        print(
            "Private overlap check: PASSED "
            f"(compared with {summary['private_profiles']} private profiles / {summary['private_jobs']} private jobs; "
            f"max shared years {summary['max_shared_years']} <= {MAX_SHARED_YEARS}, "
            f"shared 6-word profile runs {summary['shared_six_word_runs']}, "
            f"shared 8-word job runs {summary['shared_eight_word_job_runs']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
