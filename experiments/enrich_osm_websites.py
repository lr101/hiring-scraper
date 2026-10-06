"""Find official-site leads for OSM records missing a website tag."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from hiring_scraper.osm_websites import (
    fetch_wikidata_entities,
    resolve_osm_email_websites,
    referenced_entity_ids,
    resolve_osm_websites,
    select_website_enrichments,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = ROOT / "fixtures/karlsruhe-osm-candidates.csv"
DEFAULT_OSM = ROOT / "data/location-sources/run-2026-10-05/osm-radius-15km.body"
DEFAULT_CACHE = ROOT / "data/location-sources/run-2026-10-05/wikidata-osm-website-entities.json"
DEFAULT_SUGGESTIONS = ROOT / "data/location-sources/run-2026-10-05/wikidata-osm-website-suggestions.csv"
DEFAULT_EMAIL_VERIFICATIONS = ROOT / "data/location-sources/homepage-poc-2026-10/email-domain-verifications.json"


def read_candidates(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def load_or_fetch_entities(qids: list[str], cache_path: Path) -> dict:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        entities = payload.get("entities", payload)
        missing = sorted(set(qids) - set(entities))
        if not missing:
            if "entities" not in payload or not payload.get("retrieved_at"):
                cache_path.write_text(json.dumps({
                    "source": "Wikidata MediaWiki wbgetentities API",
                    "retrieved_at": retrieved_at,
                    "entity_count": len(entities),
                    "queried_entity_ids": sorted(entities),
                    "entities": entities,
                }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return entities
        fetched = fetch_wikidata_entities(missing)
        entities.update(fetched)
    else:
        entities = fetch_wikidata_entities(qids)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "source": "Wikidata MediaWiki wbgetentities API",
        "retrieved_at": retrieved_at,
        "entity_count": len(entities),
        "queried_entity_ids": sorted(entities),
        "entities": entities,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entities


def load_email_verifications(path: Path | None) -> list[dict]:
    """Load source-keyed public-page identity checks; absent evidence is not approval."""
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("verifications", payload) if isinstance(payload, dict) else payload
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def write_suggestions(path: Path, suggestions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["source_id", "company_name", "entity_id", "entity_name", "relation", "website_url",
               "website_claim_rank", "identity_score", "eligible_for_enrichment", "ineligible_reason",
               "email_verification_state", "verification_evidence_url", "method", "evidence_url",
               "osm_source_url"]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(suggestions)


def build_enrichment(existing: dict, suggestions: list[dict], observed_at: str) -> dict:
    """Apply one best eligible OSM-derived website to each still-unmatched record."""
    companies = [dict(item) for item in existing.get("companies", [])]
    by_source = {item["source_id"]: item for item in companies if item.get("source_id")}
    applied = 0
    for source_id, suggestion in select_website_enrichments(suggestions).items():
        company = by_source.get(source_id)
        if company is None:
            company = {"source_id": source_id, "career_status": "not_checked", "feeds": []}
            companies.append(company)
            by_source[source_id] = company
        if company.get("website_url"):
            continue
        company["website_url"] = suggestion["website_url"]
        resolution = {
            "method": suggestion["method"],
            "relation": suggestion["relation"],
            "identity_score": suggestion["identity_score"],
            "evidence_urls": list(dict.fromkeys(
                url for url in (suggestion.get("evidence_url"), suggestion.get("osm_source_url"),
                                suggestion.get("verification_evidence_url")) if url)),
        }
        if suggestion.get("email_verification_state"):
            resolution["email_verification_state"] = suggestion["email_verification_state"]
        if suggestion.get("website_claim_rank"):
            resolution["website_claim_rank"] = suggestion["website_claim_rank"]
        if suggestion.get("entity_id"):
            resolution["wikidata_id"] = suggestion["entity_id"]
        company["website_resolution"] = resolution
        company.setdefault("career_status", "not_checked")
        company.setdefault("feeds", [])
        applied += 1
    prior_enrichment = existing.get("website_enrichment") or {}
    try:
        applied_total = int(prior_enrichment.get("applied_count") or 0) + applied
    except (TypeError, ValueError):
        applied_total = applied
    return {**existing, "observed_at": observed_at, "companies": companies,
            "website_enrichment": {**prior_enrichment, "source": "OSM Wikidata P856 and direct email domains",
                                   "applied_count": applied_total}}


def merge_career_poc_results(existing: dict, candidates: list[dict], suggestions: list[dict],
                             results: list[dict], captures: dict, observed_at: str) -> tuple[dict, dict]:
    """Merge cached career results only when the tested host matches a selected lead."""
    selected = select_website_enrichments(suggestions)
    company_by_source = {item["source_id"]: item for item in existing.get("companies", [])
                         if item.get("source_id")}
    result_by_source = {str(item.get("osm_source_id") or item.get("source_id")): item for item in results
                        if item.get("osm_source_id") or item.get("source_id")}

    def host(value: str | None) -> str | None:
        try:
            return (urlsplit(value or "").hostname or "").casefold().removeprefix("www.") or None
        except ValueError:
            return None

    matched = []
    mismatched = 0
    for source_id, suggestion in selected.items():
        company = company_by_source.get(source_id, {})
        result = result_by_source.get(source_id)
        if not result or not company.get("website_url"):
            continue
        if host(result.get("website")) != host(company.get("website_url")):
            mismatched += 1
            continue
        matched.append(result)

    if not matched:
        return existing, {"career_results_matched": 0, "career_results_domain_mismatch_skipped": mismatched,
                          "career_html_rows": 0, "career_html_rows_accepted": 0,
                          "career_html_rows_rejected": 0}

    from experiments.finalize_karlsruhe_discovery import (
        build_enrichment as build_discovery_enrichment,
        filter_unverified_html,
    )

    shared_pages_by_parent: dict[str, list[dict]] = {}
    for result in matched:
        for page in result.get("pages", []):
            if page.get("parent"):
                shared_pages_by_parent.setdefault(page["parent"], []).append(page)
    raw_rows = accepted_rows = rejected_rows = 0
    for result in matched:
        raw, accepted, rejected = filter_unverified_html(result, captures, shared_pages_by_parent)
        raw_rows += raw
        accepted_rows += accepted
        rejected_rows += rejected
    updated = build_discovery_enrichment(candidates, matched, existing, captures, observed_at)
    return updated, {
        "career_results_matched": len(matched),
        "career_results_domain_mismatch_skipped": mismatched,
        "career_html_rows": raw_rows,
        "career_html_rows_accepted": accepted_rows,
        "career_html_rows_rejected": rejected_rows,
    }


def run(candidates_path: Path, osm_path: Path, cache_path: Path, suggestions_path: Path,
        email_verifications_path: Path | None = DEFAULT_EMAIL_VERIFICATIONS,
        enrichment_in: Path | None = None, enrichment_out: Path | None = None,
        career_run: Path | None = None) -> dict:
    candidates = read_candidates(candidates_path)
    osm = json.loads(osm_path.read_text(encoding="utf-8"))
    qids = referenced_entity_ids(osm)
    entities = load_or_fetch_entities(qids, cache_path)
    suggestions = resolve_osm_websites(candidates, osm, entities)
    email_verifications = load_email_verifications(email_verifications_path)
    suggestions.extend(resolve_osm_email_websites(candidates, email_verifications))
    write_suggestions(suggestions_path, suggestions)
    selected = select_website_enrichments(suggestions)
    summary = {
        "candidate_count": len(candidates),
        "candidates_without_osm_website": sum(not (candidate.get("website") or "").strip()
                                               for candidate in candidates),
        "referenced_wikidata_entities": len(qids),
        "official_website_suggestions": len(suggestions),
        "email_domain_suggestions": sum(item["relation"] == "osm_email_tag" for item in suggestions),
        "email_domain_identity_verifications": len(email_verifications),
        "eligible_candidates": len(selected),
        "eligible_by_relation": {relation: sum(item["relation"] == relation for item in selected.values())
                                  for relation in ("entity", "brand", "operator", "osm_url_tag",
                                                   "osm_email_tag")},
        "suggestions_csv": str(suggestions_path),
        "entity_cache": str(cache_path),
    }
    if enrichment_in and enrichment_out:
        existing = json.loads(enrichment_in.read_text(encoding="utf-8"))
        observed_at = datetime.now(timezone.utc).isoformat()
        updated = build_enrichment(existing, suggestions, observed_at)
        if career_run:
            from experiments.finalize_karlsruhe_discovery import merge_captures
            crawl_results = json.loads((career_run / "results.json").read_text(encoding="utf-8"))
            crawl_captures = merge_captures([career_run])
            updated, career_summary = merge_career_poc_results(
                updated, candidates, suggestions, crawl_results, crawl_captures, observed_at)
            summary.update(career_summary)
        enrichment_out.parent.mkdir(parents=True, exist_ok=True)
        enrichment_out.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary["enrichment_output"] = str(enrichment_out)
        summary["enrichment_applied"] = updated["website_enrichment"]["applied_count"]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--osm", type=Path, default=DEFAULT_OSM)
    parser.add_argument("--entity-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--suggestions", type=Path, default=DEFAULT_SUGGESTIONS)
    parser.add_argument("--email-verifications", type=Path, default=DEFAULT_EMAIL_VERIFICATIONS,
                        help="JSON source-keyed public-page identity checks for email-domain leads")
    parser.add_argument("--enrichment-in", type=Path)
    parser.add_argument("--enrichment-out", type=Path)
    parser.add_argument("--career-run", type=Path,
                        help="Reuse a cached results.json/http crawl, limited to selected matching website leads")
    args = parser.parse_args()
    if bool(args.enrichment_in) != bool(args.enrichment_out):
        parser.error("--enrichment-in and --enrichment-out must be used together")
    if args.career_run and not args.enrichment_out:
        parser.error("--career-run requires --enrichment-in and --enrichment-out")
    print(json.dumps(run(args.candidates, args.osm, args.entity_cache, args.suggestions,
                         args.email_verifications,
                         args.enrichment_in, args.enrichment_out, args.career_run),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
