"""Merge the bounded Karlsruhe crawl slices and refresh the app fixture."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from hiring_scraper.discovery import trusted_html_jobs
from hiring_scraper.html_jobs import extract_html_jobs


ROOT = Path(__file__).resolve().parents[1]
CAREER_STATUS = {
    "career_content_found": "career_page_found",
    "jobs_feed_found": "jobs_feed_found",
    "jobs_extracted": "jobs_extracted",
    "ats_identified": "provider_detected",
    "unresolved": "unresolved",
    "crawl_error": "unresolved",
}


def _source_id(seed: dict) -> str:
    value = seed.get("osm_source_id") or seed.get("source_id")
    if not value:
        raise ValueError(f"Seed has no OSM source ID: {seed.get('name')!r}")
    return str(value)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _status_after_quality_filter(result: dict) -> str:
    if any(board.get("feed_state") == "parsed" and board.get("provider") != "html_jobs"
           for board in result.get("boards", [])):
        return "jobs_feed_found"
    if any(board.get("provider") == "html_jobs" and board.get("feed_state") == "parsed"
           for board in result.get("boards", [])):
        return "jobs_extracted"
    if any(page.get("classification") in {"career_content", "jobposting"}
           and page.get("html_extraction_trust") != "unverified_external_source"
           for page in result.get("pages", [])):
        return "career_content_found"
    if result.get("boards"):
        return "ats_identified"
    return "unresolved"


def filter_unverified_html(result: dict, captures: dict | None = None,
                           shared_pages: list[dict] | dict[str, list[dict]] | None = None) -> tuple[int, int, int]:
    """Filter external HTML rows and optionally re-extract saved pages with employer metadata."""
    pages = result.get("pages", [])
    pages_by_url = {page.get("url"): page for page in pages}
    accepted, rejected = [], []
    prior_by_url = {item.get("url"): item for item in result.get("unverified_external_html_pages", [])}
    raw_board_rows = 0
    checked_urls = set()

    def capture_body(page):
        if not captures or not page.get("capture"):
            return None
        record = captures.get(page["capture"])
        return record[1] if record else None

    for board in result.get("boards", []):
        if board.get("provider") != "html_jobs":
            accepted.append(board)
            continue
        url = board.get("discovered_on") or board.get("board_url")
        checked_urls.add(url)
        source_page = pages_by_url.get(url, {})
        jobs = board.get("jobs", [])
        # The full crawl slices were parsed before employer metadata, heading
        # de-duplication and card-location extraction were added. Refresh saved
        # first-party HTML captures with the current parser before persisting
        # their rows; this keeps the fixture consistent with recurring scans.
        cached_page = {**source_page, "capture": source_page.get("capture") or board.get("capture")}
        body = capture_body(cached_page)
        if body is not None:
            extracted = extract_html_jobs(body, url)
            if extracted["jobs"]:
                jobs = extracted["jobs"]
                board = {**board, "jobs": jobs, "job_count": len(jobs),
                         "complete": extracted.get("complete", board.get("complete", False)),
                         "capture": cached_page.get("capture")}
        prior_count = prior_by_url.get(url, {}).get("job_count", 0)
        raw_board_rows += len(jobs) + prior_count
        safe_jobs, trust = trusted_html_jobs(result, source_page, pages, jobs)
        source_page["html_extraction_trust"] = trust
        if safe_jobs:
            accepted.append({**board, "jobs": safe_jobs, "job_count": len(safe_jobs)})
        rejected_count = prior_count + len(jobs) - len(safe_jobs)
        if rejected_count:
            rejected.append({"url": url, "job_count": rejected_count,
                             "reason": "unverified_external_source"})

    # Earlier crawl slices kept the page capture and a rejected-row count, but not
    # the rejected HTML rows. Reparse those bodies with the current extractor so
    # newly recognized hiringOrganization metadata can safely recover matching jobs.
    for url, previous in prior_by_url.items():
        if url in checked_urls:
            continue
        source_page = pages_by_url.get(url, {})
        body = capture_body(source_page)
        extracted = extract_html_jobs(body, url) if body is not None else {"jobs": [], "complete": False}
        jobs = extracted["jobs"]
        if jobs:
            raw_board_rows += len(jobs)
            safe_jobs, trust = trusted_html_jobs(result, source_page, pages, jobs)
            source_page["html_extraction_trust"] = trust
            if safe_jobs:
                accepted.append({
                    "provider": "html_jobs", "tenant": urlsplit(url).hostname,
                    "board_url": url, "feed_url": url, "evidence_url": url,
                    "evidence_kind": "static_html_job_page", "discovered_on": url,
                    "job_count": len(safe_jobs), "feed_state": "parsed",
                    "complete": extracted.get("complete", False), "jobs": safe_jobs,
                    "capture": source_page.get("capture"),
                })
            rejected_count = len(jobs) - len(safe_jobs)
        else:
            rejected_count = previous.get("job_count", 0)
            source_page["html_extraction_trust"] = "unverified_external_source"
            if body is not None:
                raw_board_rows += rejected_count
        if rejected_count:
            rejected.append({"url": url, "job_count": rejected_count,
                             "reason": "unverified_external_source"})

    # A page budget can capture a shared employer board for one company but miss
    # its detail page for a sibling OSM record. Reuse that body only when this
    # record independently links to the same external listing and the posting's
    # hiringOrganization matches this employer.
    if shared_pages and captures:
        from hiring_scraper.discovery import _source_relationship

        linked_roots = {
            page.get("url") for page in pages
            if page.get("url") and _source_relationship(result, page, pages) in {
                "linked_external", "branded_external"}
        }
        page_urls = set(pages_by_url)
        if isinstance(shared_pages, dict):
            candidate_pages = [page for parent in linked_roots for page in shared_pages.get(parent, [])]
        else:
            candidate_pages = [page for page in shared_pages if page.get("parent") in linked_roots]
        for shared_page in candidate_pages:
            url = shared_page.get("url")
            parent_url = shared_page.get("parent")
            if (not url or url in page_urls or
                    shared_page.get("classification") not in {"career_content", "jobposting"}):
                continue
            body = capture_body(shared_page)
            if body is None:
                continue
            extracted = extract_html_jobs(body, url)
            jobs = extracted["jobs"]
            if not jobs:
                continue
            source_page = {**shared_page, "html_extraction_trust": None}
            combined_pages = [*pages, source_page]
            raw_board_rows += len(jobs)
            safe_jobs, trust = trusted_html_jobs(result, source_page, combined_pages, jobs)
            if safe_jobs:
                source_page["html_extraction_trust"] = trust
                pages.append(source_page)
                pages_by_url[url] = source_page
                page_urls.add(url)
                accepted.append({
                    "provider": "html_jobs", "tenant": urlsplit(url).hostname,
                    "board_url": url, "feed_url": url, "evidence_url": url,
                    "evidence_kind": "shared_linked_html_job_detail", "discovered_on": url,
                    "job_count": len(safe_jobs), "feed_state": "parsed",
                    "complete": extracted.get("complete", False), "jobs": safe_jobs,
                    "capture": source_page.get("capture"),
                })
            rejected_count = len(jobs) - len(safe_jobs)
            if rejected_count:
                rejected.append({"url": url, "job_count": rejected_count,
                                 "reason": "unverified_external_source"})
    result["boards"] = accepted
    if rejected:
        deduplicated = {}
        for item in rejected:
            deduplicated[item.get("url")] = item
        result["unverified_external_html_pages"] = list(deduplicated.values())
        for item in deduplicated.values():
            source_page = pages_by_url.get(item.get("url"))
            if source_page is not None and source_page.get("html_extraction_trust") not in {
                    "first_party", "branded_external", "linked_external_verified"}:
                source_page["html_extraction_trust"] = "unverified_external_source"
    else:
        result.pop("unverified_external_html_pages", None)
    rejected_rows = sum(item.get("job_count", 0) for item in rejected)
    result["status"] = _status_after_quality_filter(result)
    accepted_rows = sum(len(board.get("jobs", [])) for board in accepted if board.get("provider") == "html_jobs")
    return raw_board_rows, accepted_rows, rejected_rows


def merge_results(run_dirs: list[Path], expected_seeds: list[dict]) -> list[dict]:
    expected_ids = {_source_id(seed) for seed in expected_seeds}
    output: dict[str, dict] = {}
    for run_dir in run_dirs:
        result_path = run_dir / "results.json"
        if not result_path.exists():
            raise FileNotFoundError(result_path)
        for result in _read_json(result_path):
            source_id = _source_id(result)
            if source_id not in expected_ids:
                raise ValueError(f"Unexpected source ID {source_id} in {run_dir}")
            if source_id in output:
                raise ValueError(f"Candidate {source_id} appears in more than one run")
            output[source_id] = result
    missing = expected_ids - output.keys()
    if missing:
        raise ValueError(f"Crawl slices are missing {len(missing)} website seeds; first: {sorted(missing)[:5]}")
    return [output[_source_id(seed)] for seed in expected_seeds]


def merge_captures(run_dirs: list[Path]) -> dict[str, tuple[dict, bytes, Path]]:
    captures: dict[str, tuple[dict, bytes, Path]] = {}
    for run_order, run_dir in enumerate(run_dirs):
        for metadata_path in sorted((run_dir / "http").glob("*.json")):
            record = _read_json(metadata_path)
            body_path = metadata_path.with_suffix(".body")
            if not body_path.exists():
                raise FileNotFoundError(body_path)
            body = body_path.read_bytes()
            if hashlib.sha256(body).hexdigest() != record.get("sha256"):
                raise ValueError(f"Capture hash mismatch: {metadata_path}")
            key = record.get("capture") or hashlib.sha256(record["url"].encode()).hexdigest()[:20]
            record = {**record, "merged_from_run": run_dir.name, "_merge_order": run_order}
            previous = captures.get(key)
            rank = (record.get("checked_at", ""), "reused_from" not in record, record["_merge_order"],
                    metadata_path.as_posix())
            previous_rank = ((previous[0].get("checked_at", ""), "reused_from" not in previous[0],
                              previous[0]["_merge_order"], previous[2].as_posix()) if previous else None)
            if previous is None or rank > previous_rank:
                captures[key] = (record, body, metadata_path)
    for record, _, _ in captures.values():
        record.pop("_merge_order", None)
    return captures


def _career_url(result: dict, existing: dict) -> str | None:
    pages = [page for page in result.get("pages", [])
             if page.get("classification") in {"career_content", "jobposting"} and page.get("url")
             and page.get("html_extraction_trust") != "unverified_external_source"]
    pages.sort(key=lambda page: (page.get("depth", 0) == 0, page.get("depth", 0), page.get("url", "")))
    if pages:
        return pages[0]["url"]
    boards = [board for board in result.get("boards", []) if board.get("board_url")]
    if boards:
        return boards[0]["board_url"]
    return existing.get("career_url")


def _fixture_feed(board: dict, capture_by_id: dict) -> dict | None:
    if board.get("feed_state") != "parsed" or not board.get("feed_url"):
        return None
    complete = bool(board.get("complete"))
    jobs = board.get("jobs", [])
    feed = {
        "provider": board["provider"], "tenant": board.get("tenant"),
        "board_url": board.get("board_url"), "feed_url": board["feed_url"],
        "status": ("parsed" if jobs else "complete_empty") if complete else "incomplete",
        "job_count": len(jobs), "jobs": jobs,
    }
    metadata = capture_by_id.get(board.get("capture"))
    if metadata and metadata.get("checked_at"):
        feed["last_checked_at"] = metadata["checked_at"]
    return feed


def build_enrichment(candidates: list[dict], results: list[dict], prior: dict,
                     captures: dict[str, tuple[dict, bytes, Path]], observed_at: str) -> dict:
    result_by_source = {_source_id(result): result for result in results}
    old_by_source = {item["source_id"]: item for item in prior.get("companies", [])}
    capture_by_id = {capture[0].get("capture"): capture[0] for capture in captures.values()}
    companies = []
    for candidate in candidates:
        source_id = f"{candidate['osm_type']}/{candidate['osm_id']}"
        previous = dict(old_by_source.get(source_id, {}))
        result = result_by_source.get(source_id)
        if result is None:
            if not (candidate.get("website") or previous.get("website_url")):
                previous.update(source_id=source_id, career_status="not_checked")
            elif not previous:
                previous.update(source_id=source_id, career_status="not_checked", feeds=[])
            companies.append(previous)
            continue

        result_status = CAREER_STATUS.get(result.get("status"), "unresolved")
        old_status = previous.get("career_status")
        if result_status == "unresolved" and old_status in {
                "career_page_found", "jobs_feed_found", "jobs_extracted", "provider_detected"}:
            result_status = old_status
        previous["source_id"] = source_id
        previous["career_status"] = result_status
        previous["study_status"] = result.get("status")
        previous["career_url"] = _career_url(result, previous)
        if candidate.get("website"):
            previous.setdefault("website_url", candidate["website"])

        incoming = {}
        pages_by_url = {page.get("url"): page for page in result.get("pages", [])}
        for board in result.get("boards", []):
            if not board.get("capture") and board.get("discovered_on") in pages_by_url:
                board = {**board, "capture": pages_by_url[board["discovered_on"]].get("capture")}
            feed = _fixture_feed(board, capture_by_id)
            if feed:
                incoming[(feed["provider"], feed["feed_url"])] = feed
        combined = {(feed["provider"], feed["feed_url"]): feed
                    for feed in previous.get("feeds", []) if feed.get("provider") and feed.get("feed_url")}
        combined.update(incoming)
        for feed in combined.values():
            if not feed.get("last_checked_at"):
                feed["last_checked_at"] = prior.get("observed_at") or observed_at
        previous["feeds"] = list(combined.values())

        observed_times = []
        for page in result.get("pages", []):
            metadata = capture_by_id.get(page.get("capture"))
            if metadata and metadata.get("checked_at"):
                observed_times.append(metadata["checked_at"])
        for board in result.get("boards", []):
            metadata = capture_by_id.get(board.get("capture"))
            if metadata and metadata.get("checked_at"):
                observed_times.append(metadata["checked_at"])
        if observed_times:
            previous["last_checked_at"] = max(observed_times)
        elif not previous.get("last_checked_at"):
            previous["last_checked_at"] = observed_at
        companies.append(previous)

    candidate_ids = {f"{candidate['osm_type']}/{candidate['osm_id']}" for candidate in candidates}
    companies.extend(item for source_id, item in old_by_source.items() if source_id not in candidate_ids)

    return {**prior, "observed_at": observed_at, "companies": companies}


def finalize(candidates_path: Path, seed_path: Path, scope_path: Path, run_dirs: list[Path],
             out_dir: Path, enrichment_path: Path) -> dict:
    if out_dir.exists():
        raise FileExistsError(f"Output run already exists: {out_dir}")
    with candidates_path.open(newline="", encoding="utf-8") as file:
        candidates = list(csv.DictReader(file))
    seeds = _read_json(seed_path)
    scope = _read_json(scope_path)
    if len(candidates) != scope["candidate_count"] or len(seeds) != scope["with_website_seed_count"]:
        raise ValueError("Candidate CSV, seed list and crawl scope counts do not agree")
    results = merge_results(run_dirs, seeds)
    captures = merge_captures(run_dirs)
    shared_pages_by_parent: dict[str, list[dict]] = {}
    for result in results:
        for page in result.get("pages", []):
            if page.get("parent"):
                shared_pages_by_parent.setdefault(page["parent"], []).append(page)
    raw_html_rows = accepted_html_rows = rejected_html_rows = 0
    for result in results:
        raw, accepted, rejected = filter_unverified_html(result, captures, shared_pages_by_parent)
        raw_html_rows += raw
        accepted_html_rows += accepted
        rejected_html_rows += rejected

    observed_times = [item[0].get("checked_at") for item in captures.values() if item[0].get("checked_at")]
    observed_at = max(observed_times) if observed_times else datetime.now(timezone.utc).isoformat()
    prior = _read_json(enrichment_path)
    enrichment = build_enrichment(candidates, results, prior, captures, observed_at)

    provider_counts = Counter(board["provider"] for result in results for board in result.get("boards", [])
                              if board.get("feed_state") == "parsed" and board.get("feed_url"))
    provider_jobs = Counter()
    total_jobs = 0
    for result in results:
        for board in result.get("boards", []):
            if board.get("feed_state") != "parsed" or not board.get("feed_url"):
                continue
            count = len(board.get("jobs", []))
            provider_jobs[board["provider"]] += count
            total_jobs += count
    statuses = Counter(result.get("status", "unresolved") for result in results)
    summary = {
        "scope": "Karlsruhe OSM 15 km candidate set",
        "candidate_count": len(candidates), "website_seeds": len(seeds),
        "no_website_candidates": scope["no_website_count"],
        "invalid_website_candidates": scope["invalid_website_count"],
        "unique_website_urls": scope["unique_website_urls"], "unique_hosts": scope["unique_hosts"],
        "crawled_candidates": len(results), "statuses": dict(statuses),
        "career_pages_or_providers": sum(statuses[key] for key in
                                          ("career_content_found", "jobs_feed_found", "jobs_extracted", "ats_identified")),
        "parsed_feeds": sum(provider_counts.values()), "feeds_by_provider": dict(provider_counts),
        "parsed_jobs": total_jobs, "jobs_by_provider": dict(provider_jobs),
        "html_role_rows_before_external_source_filter": raw_html_rows,
        "trusted_html_role_rows": accepted_html_rows,
        "excluded_unverified_external_role_rows": rejected_html_rows,
        "unverified_external_pages": sum(len(result.get("unverified_external_html_pages", [])) for result in results),
        "unique_http_captures": len(captures),
        "live_unique_http_urls": sum("reused_from" not in item[0] for item in captures.values()),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "input_runs": [path.name for path in run_dirs],
    }

    out_dir.mkdir(parents=True)
    (out_dir / "seeds.json").write_text(json.dumps(seeds, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / "companies.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["name", "website", "cohort", "status", "career_pages",
                                                 "providers", "feed_jobs"], lineterminator="\n")
        writer.writeheader()
        for result in results:
            writer.writerow({"name": result["name"], "website": result["website"],
                             "cohort": result.get("cohort", ""), "status": result.get("status"),
                             "career_pages": " | ".join(page["url"] for page in result.get("pages", [])
                                 if page.get("classification") in {"career_content", "jobposting"} and
                                 page.get("html_extraction_trust") != "unverified_external_source"),
                             "providers": " | ".join(board["provider"] for board in result.get("boards", [])),
                             "feed_jobs": " | ".join(str(board.get("job_count")) for board in result.get("boards", [])
                                 if board.get("feed_state") == "parsed" and board.get("job_count") is not None)})
    http_dir = out_dir / "http"
    http_dir.mkdir()
    for record, body, metadata_path in captures.values():
        key = record.get("capture") or hashlib.sha256(record["url"].encode()).hexdigest()[:20]
        body_target = http_dir / f"{key}.body"
        try:
            os.link(metadata_path.with_suffix(".body"), body_target)
        except OSError:
            body_target.write_bytes(body)
        (http_dir / f"{key}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    run_starts, run_finishes = [], []
    for run_dir in run_dirs:
        run_manifest = run_dir / "run.json"
        if run_manifest.exists():
            metadata = _read_json(run_manifest)
            if metadata.get("started_at"):
                run_starts.append(metadata["started_at"])
        run_summary = run_dir / "summary.json"
        if run_summary.exists() and _read_json(run_summary).get("finished_at"):
            run_finishes.append(_read_json(run_summary)["finished_at"])
        else:
            run_capture_times = [record.get("checked_at") for record, _, _ in captures.values()
                                 if record.get("merged_from_run") == run_dir.name and record.get("checked_at")]
            if run_capture_times:
                run_finishes.append(max(run_capture_times))
    run = {"started_at": min(run_starts) if run_starts else observed_at,
           "finished_at": max(run_finishes) if run_finishes else observed_at,
           "input_runs": [str(path) for path in run_dirs], "source_scope": str(scope_path),
           "git_revision": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                           text=True).stdout.strip(),
           "git_dirty": bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                            text=True).stdout.strip())}
    source_files = list((ROOT / "hiring_scraper").rglob("*.py")) + [Path(__file__).resolve()]
    run["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted(set(source_files))}
    (out_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")

    temp_path = enrichment_path.with_suffix(enrichment_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(enrichment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(enrichment_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=ROOT / "fixtures/karlsruhe-osm-candidates.csv")
    parser.add_argument("--seeds", type=Path, default=ROOT / "data/career-poc/seeds-karlsruhe-osm-2251.json")
    parser.add_argument("--scope", type=Path, default=ROOT / "data/career-poc/karlsruhe-osm-2251-scope.json")
    parser.add_argument("--runs", type=Path, nargs="+", default=[ROOT / f"data/career-poc/run-{value}" for value in ("25", "26", "27", "28")])
    parser.add_argument("--out", type=Path, default=ROOT / "data/career-poc/run-29")
    parser.add_argument("--enrichment", type=Path, default=ROOT / "fixtures/karlsruhe-career-enrichment.json")
    args = parser.parse_args()
    print(json.dumps(finalize(args.candidates, args.seeds, args.scope, args.runs, args.out, args.enrichment),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
