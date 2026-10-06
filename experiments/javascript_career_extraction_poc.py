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
from hiring_scraper.html_jobs import extract_html_jobs


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


def _eligible_captures(run_dir: Path, results: list[dict], metadata: dict[str, dict]):
    """Return one page context per qualifying body; avoid loading other bodies."""
    captures = {}
    for result in results:
        for page in result.get("pages", []):
            capture = page.get("capture")
            record = metadata.get(capture)
            if (not capture or not record or record.get("state") != "ok" or
                    "html" not in record.get("content_type", "").casefold()):
                continue
            lead = " ".join((page.get("url", ""), page.get("title", ""), page.get("headings", "")))
            if not CAREER_TERMS.search(lead) or page.get("ats"):
                continue
            body_path = run_dir / "http" / f"{capture}.body"
            body = body_path.read_text(encoding="utf-8", errors="replace")
            if "JobPosting" in body:
                continue
            # A capture can be associated with equivalent duplicate employer seeds.
            # Keep its stable lowest-URL-hash context so the sample remains unique.
            prior = captures.get(capture)
            if prior is None or hashlib.sha256(page["url"].encode()).hexdigest() < hashlib.sha256(prior[1]["url"].encode()).hexdigest():
                captures[capture] = (result, page, body)
    return captures


def _title_key(job: dict) -> str:
    return re.sub(r"\W+", " ", job.get("title", "").casefold()).strip()


def run(run_dir: Path, sample_size: int) -> dict:
    metadata = _capture_metadata(run_dir)
    results = _read_json(run_dir / "results.json")
    captures = _eligible_captures(run_dir, results, metadata)
    sample = sorted(captures.items(), key=lambda item: hashlib.sha256(
        (item[1][1]["url"] + "\n" + item[0]).encode()
    ).hexdigest())[:sample_size]
    markers = Counter()
    generic_embedded_jobs = 0
    structured = []
    for capture, (result, page, body) in sample:
        for name, pattern in MARKERS.items():
            markers[name] += bool(pattern.search(body))
        extracted = extract_html_jobs(body, page["url"])
        generic_embedded_jobs += sum(
            (job.get("raw_metadata") or {}).get("extraction_method") == "embedded_json"
            for job in extracted["jobs"]
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
                "extracted": card_jobs,
                "accepted": accepted,
            })

    baseline_titles = set()
    for item in structured:
        for result in results:
            for board in result.get("boards", []):
                if board.get("provider") == "html_jobs" and board.get("board_url") == item["url"]:
                    baseline_titles.update(_title_key(job) for job in board.get("jobs", []))
    accepted = [job for item in structured for job in item["accepted"]]
    accepted_by_title = {_title_key(job): job for job in accepted}
    incremental = sorted(set(accepted_by_title) - baseline_titles)
    return {
        "run_dir": str(run_dir),
        "eligible_unique_captures": len(captures),
        "sample_size": len(sample),
        "sample_rule": "one context per capture, then first N sorted by SHA-256(page URL + newline + capture ID), after career-like HTML, no ATS, no JobPosting filters",
        "markers": dict(markers),
        "embedded_json_jobs": generic_embedded_jobs,
        "structured_card_sources": [{
            key: value for key, value in item.items() if key not in {"extracted", "accepted"}
        } for item in structured],
        "structured_card_jobs": len(accepted_by_title),
        "baseline_matching_jobs": len(baseline_titles),
        "incremental_unique_jobs": len(incremental),
        "incremental_jobs": [accepted_by_title[key] for key in incremental],
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
    args = parser.parse_args()
    print(json.dumps(run(args.run_dir, args.sample_size), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
