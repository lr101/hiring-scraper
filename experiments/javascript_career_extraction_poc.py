"""Measure safe static extraction leads in a deterministic saved-capture slice."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

from hiring_scraper.discovery import trusted_html_jobs
from hiring_scraper.html_jobs import extract_html_jobs, html_job_key


MARKERS = {
    "next_data": re.compile(r"__NEXT_DATA__", re.I),
    "next_flight": re.compile(r"self\.__next_f\.push", re.I),
    "nuxt": re.compile(r"__NUXT(?:__|_DATA__)", re.I),
    "same_origin_endpoint_reference": re.compile(r"(?:/api/|graphql|\.json(?:[\"'?]|$))", re.I),
    "job_script_text": re.compile(r"<script[^>]*>[^<]{0,20000}(?:jobs|openings|vacancies|jobOffers)", re.I | re.S),
}
CAREER_TERMS = re.compile(r"career|karriere|job|stellen|vacan|position", re.I)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _capture_metadata(run_dir: Path) -> dict[str, dict]:
    return {
        record["capture"]: record
        for path in run_dir.joinpath("http").glob("*.json")
        if (record := _read_json(path)).get("capture")
    }


def _eligible_captures(results: list[dict], metadata: dict[str, dict]):
    """Return one page context per qualifying capture using saved page metadata only."""
    captures = {}
    for result in results:
        for page in result.get("pages", []):
            capture = page.get("capture")
            record = metadata.get(capture)
            if (not capture or not record or record.get("state") != "ok" or
                    "html" not in record.get("content_type", "").casefold()):
                continue
            lead = " ".join((page.get("url", ""), page.get("title", ""), page.get("headings", "")))
            if not CAREER_TERMS.search(lead) or page.get("ats") or page.get("jobposting_count", 0):
                continue
            # A capture can be associated with equivalent duplicate employer seeds.
            # Keep its stable lowest-URL-hash context so the sample remains unique.
            prior = captures.get(capture)
            if prior is None or hashlib.sha256(page["url"].encode()).hexdigest() < hashlib.sha256(prior[1]["url"].encode()).hexdigest():
                captures[capture] = (result, page)
    return captures


def _title_key(job: dict) -> str:
    return re.sub(r"\W+", " ", job.get("title", "").casefold()).strip()


def _compact_job(job: dict) -> dict:
    return {
        "title": job.get("title"), "url": job.get("url"), "location": job.get("location"),
        "identity_key": list(html_job_key(job)),
    }


def run(run_dir: Path, sample_size: int) -> dict:
    metadata = _capture_metadata(run_dir)
    results = _read_json(run_dir / "results.json")
    captures = _eligible_captures(results, metadata)
    sample = sorted(captures.items(), key=lambda item: hashlib.sha256(
        (item[1][1]["url"] + "\n" + item[0]).encode()
    ).hexdigest())[:sample_size]
    markers = Counter()
    generic_embedded_candidates = 0
    structured = []
    for capture, (result, page) in sample:
        body = (run_dir / "http" / f"{capture}.body").read_text(encoding="utf-8", errors="replace")
        for name, pattern in MARKERS.items():
            markers[name] += bool(pattern.search(body))
        extracted = extract_html_jobs(body, page["url"])
        generic_embedded_candidates += sum(
            candidate.get("method") == "embedded_json" for candidate in extracted["role_candidates"]
        )
        card_jobs = [job for job in extracted["jobs"]
                     if (job.get("raw_metadata") or {}).get("extraction_method") == "structured_job_card"]
        if card_jobs:
            accepted, trust = trusted_html_jobs(result, page, result.get("pages", []), card_jobs)
            structured.append({
                "capture": capture,
                "company": result.get("name"),
                "url": page["url"],
                "trust": trust,
                "result": result,
                "extracted": card_jobs,
                "accepted": accepted,
                "unconfirmed_candidates": [candidate for candidate in extracted["role_candidates"]
                                             if candidate.get("method") == "structured_job_card"],
            })

    baseline_jobs = []
    for item in structured:
        for board in item["result"].get("boards", []):
            if board.get("provider") == "html_jobs" and board.get("board_url") == item["url"]:
                baseline_jobs.extend(board.get("jobs", []))
    accepted = [job for item in structured for job in item["accepted"]]
    accepted_by_identity = {html_job_key(job): job for job in accepted}
    baseline_titles = {_title_key(job) for job in baseline_jobs}
    overlap_titles = sorted({_title_key(job) for job in accepted_by_identity.values()} & baseline_titles)
    new_title_roles = [job for job in accepted_by_identity.values() if _title_key(job) not in baseline_titles]
    return {
        "run_dir": str(run_dir),
        "eligible_unique_captures": len(captures),
        "sample_size": len(sample),
        "sample_rule": "one context per capture, then first N sorted by SHA-256(page URL + newline + capture ID), using saved page metadata (career-like HTML, no ATS, jobposting_count=0) before loading only the sampled bodies",
        "markers": dict(markers),
        "embedded_json_active_jobs": 0,
        "embedded_json_unconfirmed_candidates": generic_embedded_candidates,
        "structured_card_sources": [{
            "capture": item["capture"], "company": item["company"], "url": item["url"], "trust": item["trust"],
            "access": {key: metadata[item["capture"]].get(key) for key in ("state", "status", "content_type", "bytes")},
            "active_rows": [_compact_job(job) for job in item["accepted"]],
            "unconfirmed_rows": item["unconfirmed_candidates"],
        } for item in structured],
        "structured_active_identity_count": len(accepted_by_identity),
        "baseline_synthetic_heading_count": len(baseline_jobs),
        "baseline_title_overlap_count": len(overlap_titles),
        "baseline_title_overlap": overlap_titles,
        "new_stable_detail_identity_count": len(accepted_by_identity),
        "new_stable_detail_rows": [_compact_job(job) for job in accepted_by_identity.values()],
        "new_title_location_role_count": len(new_title_roles),
        "new_title_location_role_rows": [_compact_job(job) for job in new_title_roles],
        "prior_generic_hint_replacement_count": len(overlap_titles),
        "same_origin_endpoint_requests": 0,
        "browser_available": {
            "playwright": importlib.util.find_spec("playwright") is not None,
            "selenium": importlib.util.find_spec("selenium") is not None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=Path("data/career-poc/run-31"))
    parser.add_argument("--sample-size", type=int, default=250)
    parser.add_argument("--output", type=Path,
                        default=Path("data/career-poc/js-extraction-poc-2026-10/replay.json"))
    args = parser.parse_args()
    result = run(args.run_dir, args.sample_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
