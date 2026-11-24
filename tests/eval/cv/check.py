"""Self-check and baseline evaluation script for the real-CV eval dataset.

Usage:
    python -m tests.eval.cv.check

Validates schema, counts, split disjointness, strict anonymisation / PII rules,
and evaluates the v3 matcher (from recorded model outputs) over all labelled cases
using local self-contained metric helpers (does not import tests.eval.metrics).
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from hiring_scraper.matching import VERSION as MATCHER_VERSION
from tests.eval.cv import (
    CV_EVAL_DIR,
    JOBS_DIR,
    LABELS_PATH,
    PROFILES_DIR,
    load_cases,
    load_jobs,
    load_labels,
    load_profiles,
)

GENERIC_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email/at-sign", re.compile(r"@")),
    ("country phone prefix", re.compile(r"\+(?:49|41|43|44|33|31|1)\b")),
    ("phone-like digit run", re.compile(r"(?:\b0\d{2,4}[\s/.-]\d{4,}|\d{7,})")),
    (
        "social profile URL",
        re.compile(
            r"(?:https?://)?(?:www\.)?(?:linkedin\.com|xing\.com|github\.com|twitter\.com|x\.com|instagram\.com|facebook\.com)/",
            re.IGNORECASE,
        ),
    ),
)

NEAR_DUPLICATE_NGRAM = 5
NEAR_DUPLICATE_JACCARD_THRESHOLD = 0.6

REQUIRED_PROFILE_KEYS: tuple[str, ...] = (
    "desired_roles",
    "secondary_roles",
    "skills",
    "languages",
    "language_levels",
    "experience_years",
    "summary",
    "application_details",
    "cv_career_facts",
    "skill_evidence",
    "application_preferences",
    "education",
)

REQUIRED_JOB_KEYS: tuple[str, ...] = (
    "id",
    "source_url",
    "retrieved",
    "company",
    "lang",
    "title",
    "location_text",
    "description",
    "description_kind",
)

REQUIRED_LABEL_KEYS: tuple[str, ...] = (
    "case_id",
    "profile_id",
    "job_id",
    "grade",
    "should_hide",
    "tags",
    "split",
    "rationale",
)

PLATEAU_VALUES: tuple[int, ...] = (29, 59, 85)


def _iter_strings(obj: object, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(obj, str):
        found.append((prefix, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(_iter_strings(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            found.extend(_iter_strings(item, f"{prefix}[{idx}]"))
    return found


def _check_generic_pii(text: str, location: str, errors: list[str]) -> None:
    for label, pattern in GENERIC_PII_PATTERNS:
        if pattern.search(text):
            errors.append(f"{location} contains generic PII pattern ({label})")


def _word_ngrams(text: str, n: int = NEAR_DUPLICATE_NGRAM) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9äöüß]+", text.lower())
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def group_near_duplicate_jobs(
    jobs: dict[str, dict],
    *,
    n: int = NEAR_DUPLICATE_NGRAM,
    threshold: float = NEAR_DUPLICATE_JACCARD_THRESHOLD,
) -> tuple[dict[str, str], list[tuple[str, str, float]]]:
    """Group jobs whose title+description word n-gram Jaccard >= threshold.

    Jobs sharing a ``pair_id`` (translations of one posting) are always grouped,
    so a translation pair can never be split across dev and heldout.

    Returns ``(group_key_by_job_id, duplicate_pairs)`` where each job maps to the
    smallest key in its connected component; a job's key is its ``pair_id`` when
    it has one, otherwise its ``job_id``.
    """
    jids = sorted(jobs.keys())
    grams = {
        jid: _word_ngrams(f"{jobs[jid].get('title', '')} {jobs[jid].get('description', '')}", n)
        for jid in jids
    }
    parent = {jid: jid for jid in jids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    first_by_pair: dict[str, str] = {}
    for jid in jids:
        pair_id = jobs[jid].get("pair_id")
        if pair_id:
            union(first_by_pair.setdefault(str(pair_id), jid), jid)

    near_pairs: list[tuple[str, str, float]] = []
    for idx, ji in enumerate(jids):
        gi = grams[ji]
        if not gi:
            continue
        for jj in jids[idx + 1 :]:
            gj = grams[jj]
            if not gj:
                continue
            inter = len(gi & gj)
            if inter == 0:
                continue
            jaccard = inter / len(gi | gj)
            if jaccard >= threshold:
                union(ji, jj)
                near_pairs.append((ji, jj, jaccard))

    own_key = {jid: str(jobs[jid].get("pair_id") or jid) for jid in jids}
    component_key: dict[str, str] = {}
    for jid in jids:
        root = find(jid)
        component_key[root] = min(component_key.get(root, own_key[jid]), own_key[jid])
    return {jid: component_key[find(jid)] for jid in jids}, near_pairs


def _expected_split(group_key: str) -> str:
    digest = hashlib.sha256(group_key.encode("utf-8")).digest()
    return "heldout" if (int.from_bytes(digest[:4], "big") % 100) < 30 else "dev"


def validate_dataset() -> tuple[dict[str, dict], dict[str, dict], list[dict], list[str]]:
    """Validate profiles, jobs, labels, provenance, leakage, and generic PII constraints."""
    errors: list[str] = []

    provenance_path = CV_EVAL_DIR / "PROVENANCE.md"
    if not provenance_path.exists():
        errors.append(f"Missing {provenance_path}")
    else:
        prov_text = provenance_path.read_text(encoding="utf-8")
        _check_generic_pii(prov_text, "PROVENANCE.md", errors)

    profiles = load_profiles(PROFILES_DIR)
    if not profiles:
        errors.append("No profiles found")
    if "base" not in profiles:
        errors.append("Missing required 'base' profile")

    for pid, prof in profiles.items():
        if not isinstance(prof, dict):
            errors.append(f"Profile {pid!r} is not a JSON object")
            continue
        for key in REQUIRED_PROFILE_KEYS:
            if key not in prof:
                errors.append(f"Profile {pid!r} missing required key {key!r}")
        for field_path, text in _iter_strings(prof):
            _check_generic_pii(text, f"Profile {pid!r}.{field_path}", errors)

    jobs = load_jobs(JOBS_DIR)
    if len(jobs) < 150:
        errors.append(f"Expected >= 150 jobs, found {len(jobs)}")

    seen_urls: dict[str, str] = {}
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            errors.append(f"Job {jid!r} is not a JSON object")
            continue
        for key in REQUIRED_JOB_KEYS:
            if key not in job or not isinstance(job[key], str) or not job[key].strip():
                errors.append(f"Job {jid!r} missing/empty required string key {key!r}")
        if job.get("id") != jid:
            errors.append(f"Job file {jid}.json has mismatched id {job.get('id')!r}")
        if job.get("lang") not in ("de", "en"):
            errors.append(f"Job {jid!r} has invalid lang {job.get('lang')!r}")
        if job.get("description_kind") != "summary":
            errors.append(f"Job {jid!r} must have description_kind='summary', got {job.get('description_kind')!r}")
        url = job.get("source_url", "")
        if not url.startswith(("http://", "https://")):
            errors.append(f"Job {jid!r} has invalid source_url {url!r}")
        if "arbeitnow" in url.lower():
            errors.append(f"Job {jid!r} uses forbidden Arbeitnow aggregator source_url {url!r}")
        if url in seen_urls:
            errors.append(f"Duplicate source_url in {jid!r} and {seen_urls[url]!r}")
        else:
            seen_urls[url] = jid
        wa = job.get("work_arrangement")
        if wa is not None and wa not in ("remote", "hybrid", "onsite"):
            errors.append(f"Job {jid!r} has invalid work_arrangement {wa!r}")
        desc = job.get("description", "")
        if len(desc) > 2500:
            errors.append(f"Job {jid!r} description length {len(desc)} > 2500")
        if len(desc) < 100:
            errors.append(f"Job {jid!r} description suspiciously short ({len(desc)} chars)")
        _check_generic_pii(desc, f"Job {jid!r}.description", errors)

    group_keys, near_pairs = group_near_duplicate_jobs(jobs)

    labels = load_labels(LABELS_PATH)
    seen_case_ids: set[str] = set()
    job_splits: dict[str, set[str]] = defaultdict(set)
    base_labelled_jobs: set[str] = set()
    base_rationales: dict[str, str] = {}

    for idx, row in enumerate(labels):
        if not isinstance(row, dict):
            errors.append(f"Label row {idx} is not a JSON object")
            continue
        for key in REQUIRED_LABEL_KEYS:
            if key not in row:
                errors.append(f"Label row {idx} missing key {key!r}")
        cid = row.get("case_id")
        if cid in seen_case_ids:
            errors.append(f"Duplicate case_id {cid!r}")
        seen_case_ids.add(str(cid))

        pid = row.get("profile_id")
        jid = row.get("job_id")
        if pid not in profiles:
            errors.append(f"Label {cid!r} references unknown profile_id {pid!r}")
        if jid not in jobs:
            errors.append(f"Label {cid!r} references unknown job_id {jid!r}")
        if pid == "base" and isinstance(jid, str):
            base_labelled_jobs.add(jid)
            if isinstance(row.get("rationale"), str):
                base_rationales[jid] = row["rationale"].strip()

        grade = row.get("grade")
        if type(grade) is not int or grade not in (0, 1, 2, 3):
            errors.append(f"Label {cid!r} has invalid grade {grade!r}")
        if type(row.get("should_hide")) is not bool:
            errors.append(f"Label {cid!r} has non-bool should_hide {row.get('should_hide')!r}")
        elif row.get("should_hide") and isinstance(grade, int) and grade >= 2:
            errors.append(f"Label {cid!r} has should_hide=True with grade={grade}")
        tags = row.get("tags")
        if not isinstance(tags, list) or not tags:
            errors.append(f"Label {cid!r} must have non-empty tags list")
        elif "applied" in tags:
            errors.append(f"Label {cid!r} uses deprecated 'applied' tag; use 'target_role'")
        rationale = row.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"Label {cid!r} must have non-empty rationale")
        else:
            _check_generic_pii(rationale, f"Label {cid!r}.rationale", errors)

        sp = row.get("split")
        if sp not in ("dev", "heldout"):
            errors.append(f"Label {cid!r} has invalid split {sp!r}")
        elif isinstance(jid, str) and jid in group_keys:
            job_splits[jid].add(sp)
            expected = _expected_split(group_keys[jid])
            if sp != expected:
                errors.append(
                    f"Label {cid!r} split={sp!r} does not match stable group hash "
                    f"(group={group_keys[jid]!r}, expected={expected!r})"
                )

    for row in labels:
        if isinstance(row, dict) and row.get("profile_id") != "base":
            jid = row.get("job_id")
            rat = (row.get("rationale") or "").strip() if isinstance(row.get("rationale"), str) else ""
            if isinstance(jid, str) and jid in base_rationales and rat == base_rationales[jid]:
                errors.append(
                    f"Variant label {row.get('case_id')!r} copies its base rationale verbatim"
                )

    missing_base = sorted(set(jobs) - base_labelled_jobs)
    if missing_base:
        errors.append(f"{len(missing_base)} jobs missing base profile label: {missing_base[:5]}")
    leaked_splits = [jid for jid, sps in job_splits.items() if len(sps) > 1]
    if leaked_splits:
        errors.append(f"Jobs present in both dev and heldout splits: {leaked_splits}")

    for ji, jj, jaccard in near_pairs:
        splits_i = job_splits.get(ji, set())
        splits_j = job_splits.get(jj, set())
        if splits_i and splits_j and splits_i != splits_j:
            errors.append(
                f"Cross-split near-duplicate jobs {ji!r} ({sorted(splits_i)}) and "
                f"{jj!r} ({sorted(splits_j)}): 5-gram Jaccard={jaccard:.3f}"
            )

    return profiles, jobs, labels, errors


# ---------------------------------------------------------------------------
# Local self-contained metric helpers (no import from tests.eval.metrics)
# ---------------------------------------------------------------------------

def _rank_rows(rows: list[dict]) -> list[dict]:
    """Rank visible jobs above hidden jobs, descending by score, deterministic tiebreak."""
    return sorted(
        rows,
        key=lambda r: (0 if r["eligible"] else 1, -float(r["score"]), str(r["id"])),
    )


def _ndcg_at_k(rows: list[dict], k: int = 10) -> float:
    if not rows:
        return 0.0
    ranked = _rank_rows(rows)[:k]
    dcg = 0.0
    for idx, r in enumerate(ranked):
        gain = 0.0 if not r["eligible"] else float((2 ** int(r["grade"])) - 1)
        dcg += gain / math.log2(idx + 2)
    ideal_grades = sorted((int(r["grade"]) for r in rows), reverse=True)[:k]
    idcg = sum(((2 ** g) - 1) / math.log2(idx + 2) for idx, g in enumerate(ideal_grades))
    return (dcg / idcg) if idcg > 0 else 0.0


def _pairwise_auc(rows: list[dict]) -> float:
    eff = [
        (int(r["grade"]), float(r["score"]) if r["eligible"] else float(r["score"]) - 101.0)
        for r in rows
    ]
    concordant = 0.0
    total = 0
    n = len(eff)
    for i in range(n):
        gi, si = eff[i]
        for j in range(i + 1, n):
            gj, sj = eff[j]
            if gi == gj:
                continue
            total += 1
            if (gi > gj and si > sj) or (gj > gi and sj > si):
                concordant += 1.0
            elif si == sj:
                concordant += 0.5
    return (concordant / total) if total > 0 else 0.5


def _hide_metrics(rows: list[dict]) -> tuple[float, float, float]:
    tp = sum(1 for r in rows if r["should_hide"] and not r["eligible"])
    fp = sum(1 for r in rows if (not r["should_hide"]) and (not r["eligible"]))
    fn = sum(1 for r in rows if r["should_hide"] and r["eligible"])
    good = [r for r in rows if int(r["grade"]) >= 2]
    good_hidden = sum(1 for r in good if not r["eligible"])

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    ghr = good_hidden / len(good) if good else 0.0
    return precision, recall, ghr


def _plateau_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"v1_plateau_share": 0.0, "v1_plateau_counts": {}, "modal_score": None, "modal_share": 0.0, "distinct": 0}
    rounded = [int(round(float(r["score"]))) for r in rows]
    counts = Counter(rounded)
    v1_counts = {p: counts.get(p, 0) for p in PLATEAU_VALUES}
    v1_share = sum(v1_counts.values()) / len(rows)
    modal_score, modal_n = counts.most_common(1)[0]
    return {
        "v1_plateau_share": v1_share,
        "v1_plateau_counts": v1_counts,
        "modal_score": modal_score,
        "modal_share": modal_n / len(rows),
        "distinct": len(counts),
    }


def _lang_means(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for lang in ("de", "en"):
        vals = [float(r["score"]) for r in rows if r["lang"] == lang]
        out[lang] = (sum(vals) / len(vals)) if vals else 0.0
    return out


def _summarize_slice(rows: list[dict]) -> dict:
    by_prof: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_prof[r["profile_id"]].append(r)
    prof_ndcgs = [_ndcg_at_k(pr, 10) for pr in by_prof.values()]
    prof_aucs = [_pairwise_auc(pr) for pr in by_prof.values()]
    hp, hr, ghr = _hide_metrics(rows)
    return {
        "n": len(rows),
        "ndcg10_macro": sum(prof_ndcgs) / len(prof_ndcgs) if prof_ndcgs else 0.0,
        "ndcg10_base": _ndcg_at_k(by_prof.get("base", []), 10) if "base" in by_prof else 0.0,
        "auc_macro": sum(prof_aucs) / len(prof_aucs) if prof_aucs else 0.5,
        "auc_all": _pairwise_auc(rows),
        "hide_precision": hp,
        "hide_recall": hr,
        "good_hidden_rate": ghr,
        "lang_means": _lang_means(rows),
        "plateau": _plateau_stats(rows),
    }


def _job_category(tags: list[str]) -> str:
    if "target_role" in tags:
        return "target_role"
    if "fitting" in tags:
        return "fitting"
    if "near_miss" in tags:
        return "near_miss"
    if "non_fitting" in tags:
        return "non_fitting"
    return "other"


def main() -> int:
    profiles, jobs, labels, errors = validate_dataset()

    print("=== CV EVAL DATASET VALIDATION ===")
    if errors:
        print(f"FAILED with {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASSED: schema, split disjointness, description limits, and strict PII/anonymisation checks (0 errors).\n")

    # Counts summary
    base_rows = [r for r in labels if r["profile_id"] == "base"]
    cat_counts = Counter(_job_category(r["tags"]) for r in base_rows)
    job_lang_counts = Counter(jobs[r["job_id"]]["lang"] for r in base_rows)
    job_grade_counts = Counter(r["grade"] for r in base_rows)
    job_hide_counts = Counter(r["should_hide"] for r in base_rows)
    job_split_counts = Counter(r["split"] for r in base_rows)
    company_counts = Counter(jobs[r["job_id"]]["company"] for r in base_rows)

    case_lang_counts = Counter(jobs[r["job_id"]]["lang"] for r in labels)
    case_grade_counts = Counter(r["grade"] for r in labels)
    case_hide_counts = Counter(r["should_hide"] for r in labels)
    case_split_counts = Counter(r["split"] for r in labels)
    case_prof_counts = Counter(r["profile_id"] for r in labels)
    case_tag_counts = Counter(t for r in labels for t in r["tags"])

    print("=== DATASET COUNTS ===")
    print(f"Profiles ({len(profiles)}): {', '.join(sorted(profiles))}")
    print(f"Distinct real public jobs: {len(jobs)} across {len(company_counts)} unique companies")
    print(f"  Jobs by category : {dict(sorted(cat_counts.items()))}")
    print(f"  Jobs by lang     : {dict(sorted(job_lang_counts.items()))}")
    print(f"  Jobs by grade    : {dict(sorted(job_grade_counts.items()))}")
    print(f"  Jobs by hide     : {dict(sorted(job_hide_counts.items()))}")
    print(f"  Jobs by split    : {dict(sorted(job_split_counts.items()))}")
    print(f"  Top 15 companies : {company_counts.most_common(15)}")
    print(f"Labelled cases: {len(labels)}")
    print(f"  Cases by profile : {dict(sorted(case_prof_counts.items()))}")
    print(f"  Cases by lang    : {dict(sorted(case_lang_counts.items()))}")
    print(f"  Cases by grade   : {dict(sorted(case_grade_counts.items()))}")
    print(f"  Cases by hide    : {dict(sorted(case_hide_counts.items()))}")
    print(f"  Cases by split   : {dict(sorted(case_split_counts.items()))}")
    print(f"  Cases by tag     : {dict(sorted(case_tag_counts.items()))}\n")

    # Run the v3 matcher over all cases (recorded local-model outputs).
    from tests.eval.run import _ACTIVE_DATASET, get_matcher
    _ACTIVE_DATASET[0] = "private"
    match_job = get_matcher("v3")
    cases = load_cases()
    evaluated: list[dict] = []
    for c in cases:
        res = match_job(c["job"], c["profile"])
        evaluated.append(
            {
                "id": c["id"],
                "profile_id": c["profile_id"],
                "job_id": c["job_id"],
                "title": c["job"]["title"],
                "company": c["job"]["company"],
                "lang": c["lang"],
                "split": c["split"],
                "grade": c["grade"],
                "should_hide": c["should_hide"],
                "tags": c["tags"],
                "rationale": c["rationale"],
                "score": float(res["score"]),
                "eligible": bool(res["eligible"]),
                "fit_tier": res.get("fit_tier"),
                "conflicts": list(res.get("conflicts") or []),
                "reasons": list(res.get("reasons") or []),
            }
        )

    print(f"=== MATCHER EVALUATION (hiring_scraper.matching {MATCHER_VERSION}) ===")
    for label_name, subset in [
        ("OVERALL (all cases)", evaluated),
        ("BASE profile only", [r for r in evaluated if r["profile_id"] == "base"]),
        ("SPLIT: dev", [r for r in evaluated if r["split"] == "dev"]),
        ("SPLIT: heldout", [r for r in evaluated if r["split"] == "heldout"]),
    ]:
        m = _summarize_slice(subset)
        pl = m["plateau"]
        print(
            f"{label_name:<22} | N={m['n']:>3} | nDCG@10(macro/base)={m['ndcg10_macro']:.4f}/{m['ndcg10_base']:.4f} | "
            f"AUC(all/macro)={m['auc_all']:.4f}/{m['auc_macro']:.4f} | "
            f"HideP={m['hide_precision']:.4f} HideR={m['hide_recall']:.4f} GoodHid={m['good_hidden_rate']:.4f} | "
            f"Mean(DE/EN)={m['lang_means']['de']:.2f}/{m['lang_means']['en']:.2f} | "
            f"Plateau(29/59/85)={pl['v1_plateau_share']*100:.1f}% {pl['v1_plateau_counts']} "
            f"(modal={pl['modal_score']}@{pl['modal_share']*100:.1f}%, distinct={pl['distinct']})"
        )

    print("\nPer-profile breakdown:")
    for pid in sorted(profiles):
        sub = [r for r in evaluated if r["profile_id"] == pid]
        hp, hr, ghr = _hide_metrics(sub)
        lm = _lang_means(sub)
        pl = _plateau_stats(sub)
        print(
            f"  {pid:<22} N={len(sub):>3} | nDCG@10={_ndcg_at_k(sub, 10):.4f} | AUC={_pairwise_auc(sub):.4f} | "
            f"HideP={hp:.4f} HideR={hr:.4f} GoodHid={ghr:.4f} | "
            f"Mean(DE/EN)={lm['de']:.2f}/{lm['en']:.2f} | Plateau={pl['v1_plateau_share']*100:.1f}%"
        )

    print("\nMean score by grade (all cases / DE / EN):")
    for g in (0, 1, 2, 3):
        sub = [r for r in evaluated if r["grade"] == g]
        de_s = [r["score"] for r in sub if r["lang"] == "de"]
        en_s = [r["score"] for r in sub if r["lang"] == "en"]
        hid_n = sum(1 for r in sub if not r["eligible"])
        print(
            f"  grade={g} | N={len(sub):>3} | mean={sum(r['score'] for r in sub)/len(sub):.2f} | "
            f"DE={sum(de_s)/len(de_s) if de_s else 0:.2f} (n={len(de_s)}) | "
            f"EN={sum(en_s)/len(en_s) if en_s else 0:.2f} (n={len(en_s)}) | hidden={hid_n}/{len(sub)}"
        )

    # Diagnostic: most striking failure buckets
    print("\n=== STRIKING FAILURES DIAGNOSTIC ===")
    good_hidden = sorted(
        [r for r in evaluated if r["grade"] >= 2 and not r["eligible"]],
        key=lambda r: (-r["grade"], r["score"], r["id"]),
    )
    print(f"\n1) Good/Target jobs (grade >= 2) wrongly HIDDEN ({len(good_hidden)} cases):")
    for r in good_hidden[:10]:
        print(f"  - {r['id']} | g={r['grade']} lang={r['lang']} score={r['score']:.1f} | {r['company']} — {r['title']} | conflicts={r['conflicts'][:2]}")

    missed_hides = sorted(
        [r for r in evaluated if r["should_hide"] and r["eligible"]],
        key=lambda r: (-r["score"], r["id"]),
    )
    print(f"\n2) Hard-hide CEFR jobs (should_hide=True) NOT hidden ({len(missed_hides)} cases):")
    for r in missed_hides[:10]:
        print(f"  - {r['id']} | g={r['grade']} lang={r['lang']} score={r['score']:.1f} | {r['company']} — {r['title']} | {r['rationale'][:110]}")

    high_nonfit = sorted(
        [r for r in evaluated if r["grade"] == 0 and r["eligible"]],
        key=lambda r: (-r["score"], r["id"]),
    )
    print(f"\n3) Non-fitting jobs (grade=0, eligible) with HIGHEST scores (top 10):")
    for r in high_nonfit[:10]:
        print(f"  - {r['id']} | lang={r['lang']} score={r['score']:.1f} tags={r['tags']} | {r['company']} — {r['title']}")

    low_good = sorted(
        [r for r in evaluated if r["grade"] == 3],
        key=lambda r: (r["score"], r["id"]),
    )
    print(f"\n4) Strong fitting / target jobs (grade=3) with LOWEST scores (bottom 10):")
    for r in low_good[:10]:
        print(f"  - {r['id']} | lang={r['lang']} score={r['score']:.1f} elig={r['eligible']} | {r['company']} — {r['title']} | conflicts={r['conflicts'][:2]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
