# Plan: homepage recovery and JavaScript career extraction PoCs

The current stack has a conservative OSM/Wikidata P856 resolver for Karlsruhe records missing OSM website tags, plus a bounded static HTML career crawler. The user asked to explore additional ways to identify employer homepages and extract jobs from JavaScript-heavy pages, try wacky options, measure result-set improvements, and generalize the best methods into reusable software-stack code.

## Global constraints

- Preserve source provenance and distinguish suggestions from verified employer domains. Do not silently turn weak evidence into an automatic homepage.
- Keep collection bounded, repeatable, and respectful of access controls. Do not bypass authentication, CAPTCHAs, robots policy, or rate limits.
- Job postings and script payloads are untrusted. Do not evaluate arbitrary JavaScript in the main worker. Any external endpoints must be discovered from employer pages, restricted to safe read-only same-origin requests, and pass the existing URL/network safety policy.
- Report denominators, exact cohort, unique incremental results, false matches/rejections, request or compute cost, and generalization limits. Prefer saved captures and fixtures; keep live probes small and paced.
- Preserve the existing implementation and unrelated user changes. Implement only a method that measurably improves results or safely broadens supported formats.

## Task 1: recover additional company homepages

Investigate multiple additional programmatic sources or matching ideas against the existing Karlsruhe candidates that still lack domains. Test ideas on a labelled, bounded sample or the complete saved candidate set when cached data permits. Compare precision and unique additional verified homepage leads with the existing Wikidata-linked P856 and name-matched OSM URL baseline. Promote the best evidence-backed method into reusable code compatible with the current enrichment/import path; keep uncertain matches as auditable suggestions. Write findings to `reports/homepage-discovery-poc.md` and tests for the reusable code.

## Task 2: extract jobs from JavaScript-heavy career pages

Investigate static framework hydration/state parsing, public JSON data endpoints discoverable from captured pages, and browser rendering as competing approaches. Evaluate saved crawl captures first and use only a small live sample if required. Compare new unique valid jobs against the current parser, validate employer/location/posting identity and exclude hidden/template/closed/foreign rows, then add the safest reusable extractor that improves the current parser. Keep arbitrary JS evaluation out of the default worker. Write findings to `reports/javascript-career-extraction-poc.md` and tests for the reusable code.

## Task compatibility scan

| Pair / task | Shared output or interface | Pre-flight result |
|---|---|---|
| Task 1 × Task 2 | Both preserve source evidence; no shared parser interface is required | Independent work; run sequentially because both use one checkout |
| Task 1 | Website evidence → existing enrichment/import path | Must not bypass provenance or automatic URL safety checks |
| Task 2 | Page captures → existing `extract_html_jobs` pipeline | Must retain job provenance, employer checks, and existing deduplication |
