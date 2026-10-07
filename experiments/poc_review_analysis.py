"""Summarize committed POC evidence without crawling or changing app data.

Run from the repository root:
python3 -m experiments.poc_review_analysis --output reports/poc-review-analysis.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "crawl": "data/career-poc/run-31/results.json",
    "crawl_summary": "data/career-poc/run-31/summary.json",
    "homepage": "data/location-sources/homepage-poc-2026-10/summary.json",
    "javascript": "data/career-poc/js-extraction-poc-2026-10/replay.json",
    "matching": "reports/cv-profile-board-evaluation.json",
    "hydration": "reports/profile-matching-poc-results.json",
}


def analyze() -> dict:
    data = {key: json.loads((ROOT / name).read_text()) for key, name in INPUTS.items()}
    crawl = data["crawl"]
    summary = data["crawl_summary"]
    boards = [(company, board) for company in crawl for board in company["boards"]]
    parsed = [(company, board) for company, board in boards if board.get("feed_state") == "parsed"]
    jobs = [(company, board, job) for company, board in parsed for job in board.get("jobs", [])]
    statuses = Counter(company["status"] for company in crawl)
    by_provider = Counter(board["provider"] for _, board, _ in jobs)
    # Reconcile independent aggregations before publishing derived numbers.
    if (len(crawl) != summary["crawled_candidates"] or len(jobs) != summary["parsed_jobs"]
            or len(parsed) != summary["parsed_feeds"] or dict(statuses) != summary["statuses"]
            or dict(by_provider) != summary["jobs_by_provider"]):
        raise ValueError("Crawl results and committed summary disagree")
    if any(not job.get("id") or not job.get("url") for _, _, job in jobs):
        raise ValueError("Accepted posting rows require source IDs and URLs")

    def feed_key(board):
        return board["provider"], board.get("tenant"), board.get("feed_url")

    unsupported = {}
    for provider in sorted({board["provider"] for _, board in boards}):
        rows = [(company, board) for company, board in boards
                if board["provider"] == provider and board.get("feed_state") == "not_supported"]
        if rows:
            unsupported[provider] = {
                "board_observations": len(rows),
                "raw_distinct_tenant_labels": len({board.get("tenant") for _, board in rows}),
                "tenant_labels": sorted({str(board.get("tenant")) for _, board in rows}),
                "osm_source_records": len({company["osm_source_id"] for company, _ in rows}),
            }
    unresolved = [company for company in crawl if company["status"] == "unresolved"]
    descriptions = Counter(
        "missing" if not job.get("description") else
        "under_350_characters" if len(job["description"]) < 350 else "at_least_350_characters"
        for _, _, job in jobs
    )
    urls = {job["url"] for _, _, job in jobs}
    identities = {(board["provider"], board.get("tenant"), str(job["id"])) for _, board, job in jobs}
    final = data["matching"]["pools"]["expanded-heldout"]["algorithms"]
    current = final["current_rules"]
    hits = round(current["pooled_recall"] * current["relevant_records"])
    js = data["javascript"]
    hydration = data["hydration"]
    return {
        "study_date": "2026-10-07",
        "method": "Offline aggregation of committed derived artifacts; no HTTP or model calls.",
        "input_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in INPUTS.values()},
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": [
            "Snapshots differ by date, source population, and algorithm version; counts are not additive.",
            "Exact URL/source-ID repetition is observation duplication, not verified legal-entity linkage.",
            "Unsupported tenant labels include asset, login, and generic routes; they are unverified leads.",
            "Description length measures field coverage, not requirement-extraction accuracy.",
            "Matching judgments are model-assisted and pooled; they do not establish market recall.",
            "No new scraper, embedding, reranker, or company-resolution improvement was benchmarked.",
        ],
        "company_discovery": {
            "osm_candidates": summary["candidate_count"],
            "website_seed_records": len(crawl),
            "missing_osm_website_records": summary["no_website_candidates"],
            "positive_page_feed_provider_records": summary["career_pages_or_providers"],
            "statuses": dict(sorted(statuses.items())),
            "unresolved_initial_page_states": dict(sorted(Counter(
                company["pages"][0]["fetch_state"] if company["pages"] else "no_page"
                for company in unresolved).items())),
            "unresolved_records_reaching_six_pages": sum(len(company["pages"]) >= 6 for company in unresolved),
            "homepage_poc": data["homepage"]["cached_methods"],
            "homepage_poc_email_identity_checks": data["homepage"]["email_http_validation"],
        },
        "job_extraction": {
            "accepted_source_rows": len(jobs),
            "exact_distinct_posting_urls": len(urls),
            "exact_distinct_provider_tenant_posting_ids": len(identities),
            "repeated_url_observations": len(jobs) - len(urls),
            "repeated_url_observation_fraction": round((len(jobs) - len(urls)) / len(jobs), 6),
            "parsed_feed_observations": len(parsed),
            "exact_distinct_provider_tenant_feed_urls": len({feed_key(board) for _, board in parsed}),
            "incomplete_feed_observations": sum(not board.get("complete") for _, board in parsed),
            "exact_distinct_incomplete_feeds": len({feed_key(board) for _, board in parsed if not board.get("complete")}),
            "descriptions": dict(sorted(descriptions.items())),
            "short_or_missing_descriptions_by_provider": dict(sorted(Counter(
                board["provider"] for _, board, job in jobs if len(job.get("description") or "") < 350).items())),
            "rows_without_location_text_or_array": sum(not job.get("location") and not job.get("locations") for _, _, job in jobs),
            "html_extraction_methods": dict(sorted(Counter(
                (job.get("raw_metadata") or {}).get("extraction_method", "unknown")
                for _, board, job in jobs if board["provider"] == "html_jobs").items())),
            "unsupported_provider_leads": unsupported,
            "personio_failed_board_observations": sum(board["provider"] == "personio" and board.get("feed_state") == "http_error" for _, board in boards),
            "lever_capped_tenants": sorted({board["tenant"] for _, board in parsed
                                            if board["provider"] == "lever" and not board.get("complete")}),
            "javascript_slice": {key: js[key] for key in (
                "sample_size", "eligible_unique_captures", "embedded_json_active_jobs",
                "new_stable_detail_identity_count", "new_title_novelty_count")},
            "older_hydration_poc": {
                "rule_version": hydration["rule_version"],
                "detail_pass": hydration["detail_pass"],
                "description_count_increment": hydration["after"]["coverage"]["description"] - hydration["before"]["coverage"]["description"],
            },
        },
        "profile_matching": {
            "locked_version": data["matching"]["selection"]["rule_version"],
            "post_test_recheck_version": data["matching"]["post_test_correctness_recheck"]["rule_version"],
            "final_pool_algorithms": final,
            "board_counts": data["matching"]["board"]["counts"],
            "current_rules_final_false_positive_count": current["returned"] - hits,
            "current_rules_final_missed_relevant_count": current["relevant_records"] - hits,
            "fusion_limitation": "Fusion ranks only current_rules candidates; inclusion and hence recall are unchanged.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {args.output}: {result['job_extraction']['accepted_source_rows']} source rows; "
          f"{result['job_extraction']['repeated_url_observations']} repeated URL observations")


if __name__ == "__main__":
    main()
