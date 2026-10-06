# Profile matching and enrichment PoC — 2026-10-06

## What is integrated

`/profile` saves explicit skills, desired roles, work styles, employment types, experience levels, relevant years, working languages and excluded title terms. Pasted CV text suggests skills for review; the app does not persist that text, infer personal characteristics, or call an external AI service. This remains a single-workspace app without account isolation. PDF/DOCX import and multi-user authentication are future work.

The jobs page can select a profile, choose an overlap threshold, and include/exclude jobs with incomplete evidence. Known preference conflicts are excluded. Geographic eligibility is unchanged: work location in the selected radius or explicitly remote. Matching and filtering happen before pagination. The detail page explains matched skills, other skill mentions, conflicts, unknowns and source excerpts.

The score is a heuristic signal of interest/skill overlap, not a hiring probability. With both skills and role interests, skill overlap contributes up to 55 points and title/role agreement 45. Skills use a symmetric overlap ratio; a skills-only profile can reach 100. Short/missing descriptions cap the score at 60. Role groups include English/German aliases, with separate backend/frontend focus. Mentioned skills are not automatically mandatory. Only explicit minimum experience and confident required-language phrases can cause those conflicts. Missing profile evidence is unknown. Multi-valued employment/experience levels accept an intersecting preference.

## Sources and options explored

- **Plain keyword baseline:** rank source descriptions/titles by exact user terms. Cheap, but synonyms and role context are lost; generic Excel/SAP mentions can elevate unrelated technical jobs.
- **Evidence-backed rules, integrated:** a small multilingual vocabulary (technical and nontechnical), explicit title/role interests, constraint checks and missing-data handling. Every derived skill has a source excerpt. Custom profile skills also search structured skills/qualifications. No additional runtime dependencies or model credentials.
- **Structured job requirements, integrated:** HTML JSON-LD and schema.org feeds now preserve skills, qualifications, education and experience fields. [Schema.org JobPosting](https://schema.org/JobPosting) supports these fields, including structured months of experience. Derived signals are stored separately from employer source metadata and refreshed on source changes.
- **Structured detail hydration, integrated:** fetch a sparse job's original URL through the existing robots-aware, paced public HTTP client. Accept only an unambiguous posting with matching title and original/final canonical URL. Changed role qualifiers remain significant; gender suffixes can vary. Source redirects and relative structured URLs are supported.
- **Scoped HTML detail hydration, integrated:** matching single H1 inside main/article, a job detail route, and recognizable role sections. Ignore hidden/navigation/form/aside content and stop before other-job sections. Shared fragment/overview pages remain incomplete. Broader whole-page extraction was rejected because sampled pages contain many unrelated jobs and navigation terms.
- **ESCO taxonomy, connectivity PoC:** the official search endpoint returned HTTP 200 for `software developer`, with 490 occupation results and relevant top labels. [ESCO’s API](https://esco.ec.europa.eu/en/use-esco/use-esco-services-api/esco-web-service-api) exposes occupation/skill concepts for machine access. Pinning its multilingual dataset locally is the next vocabulary expansion; the current small dictionary is not an ESCO implementation.
- **Embeddings/LLM extraction:** deferred, not benchmarked. They may help synonyms and nuanced requirements, but need a labelled evaluation set and evidence validation before they can safely influence hard filters. Add them as optional extractors behind the same versioned signal contract.

## Measurements

The repeatable fixture contains 1,664 posting rows and 283 deduplicated Karlsruhe-or-remote rows. These are a dated, selected research corpus, not a national coverage benchmark.

| Field/signal | Before detail hydration | After the bounded 20-page replay |
|---|---:|---:|
| Any description | 1,352 | 1,359 |
| Description at least 350 characters | 1,185 | 1,192 |
| Recognized skill evidence | 701 | 706 |
| Explicit minimum experience | 15 | 16 |
| Raw employment type | 1,238 | 1,238 |
| Raw experience level | 422 | 422 |
| Salary | 50 | 50 |

The live JSON-LD-only pass improved **2/20** postings. Replaying those captured pages with the scoped HTML fallback improved **7/20**: two structured pages and five HTML pages. The latter included two event internships, Finance & Office Manager, an electrical assembly role and an HVAC role. One page with a title expanded by “in Karlsruhe” was deliberately rejected by the strict identity check. Many overview/card pages still have no verified full role description. These results measure this sample; the remaining incomplete population has not been fully hydrated.

Two synthetic demo profiles compare keyword ranking with the integrated pipeline. The stored JSON contains the final source-code hash, field coverage, counts, timing and both top-ten lists. Software development produced four strong-overlap rows (including one repeated employer establishment) and accounting five. The accounting top results include Group Accountant and Tax, Buchhalter und Zahlen Stratege, payroll/accounting roles and Mitarbeiter:in Buchhaltung & Accounting. The keyword baseline also elevated an unrelated CIAM developer and IAM engineer because of generic SAP/Excel mentions. Ranking remains heuristic; this is a manual sanity check, not a precision/recall validation against a labelled benchmark.

## Repeat and deploy

Use a **fresh** isolated SQLite path for fixture evaluation; the script refuses an existing DB so previously enriched data or user profiles cannot change the baseline:

```bash
poc_directory=$(mktemp -d /tmp/hiring-profile-poc.XXXXXX)
DATABASE_URL="sqlite:///$poc_directory/poc.sqlite3" \
  .venv/bin/python -m experiments.profile_matching_poc \
  --output reports/profile-matching-poc-results.json
```

Add `--fetch-details` to try 20 live pages. HTTP results can change over time. When local HTTP captures and their ignored `.body` files are available, add `--replay-details /tmp/hiring-scraper-enrichment` for an offline replay. Full response bodies are not committed. Run the local backfill twice to verify that the second pass updates zero unchanged records.

Every seed import, company crawl and recurring feed ingestion computes versioned enrichment. The jobs API recomputes stale/missing signals without network requests. A source fingerprint includes all matching inputs, so changed job content invalidates stored signals. Verified detail evidence is retained only across an unchanged original listing summary or a missing description; newly supplied changed descriptions replace it. Source/detail structured requirements remain separate. Detail attempts wait seven days before retrying, including failed/blocked attempts. One malformed page cannot stop the pass; no incomplete read changes job lifecycle/closure.

```bash
# Local signals only, with DATABASE_URL configured for the target database:
.venv/bin/python -m hiring_scraper.app.enrichment
# Bounded public detail reads:
.venv/bin/python -m hiring_scraper.app.enrichment --fetch-details --limit 20
# Optional hourly worker in the same server image:
.venv/bin/python -m hiring_scraper.app.enrichment --fetch-details --limit 20 --watch
# Compose includes a matching optional service:
docker compose --profile enrichment up -d
```

Existing SQLite development data is upgraded additively during API startup/fixture import. PostgreSQL deployments apply Alembic migration `0009_profiles_enrichment` through the existing migration service. Docker execution remains untested in this environment; the Python worker and migration path are exercised directly.

## Remaining quality work

1. Expand the vocabulary through pinned ESCO skills/occupations plus employer-specific aliases. Current recognized skill coverage is only 42% of this corpus; absence means unknown.
2. Hydrate a larger, stratified sample across employers/providers and review attribution, optional/mandatory requirements, multilingual phrasing and remote country restrictions.
3. Unify employer identities across OSM establishments. Some rows such as synyx and its event location refer to the same job; current provider deduplication does not eliminate every cross-establishment duplicate.
4. Create a labelled profile/job test set, including career changes and nontechnical roles, before tuning weights or introducing embeddings.
5. Add authenticated ownership, document import, user corrections and saved matching preferences before multi-user deployment. Salary matching needs normalized currency/period and much better source coverage.

See [machine-readable results](profile-matching-poc-results.json) and the matching/integration/pipeline regression tests.
