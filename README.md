# Radius — German hiring discovery

A company and job discovery app built on the Karlsruhe research POC. Its current OSM 15 km snapshot and career-discovery results were observed on **2026-10-05 UTC**.

The app has two location rules: companies match their mapped establishment point, while jobs match a work location in the search radius **or** an explicitly remote role. A local job can therefore appear even when its employer's office is outside the company list. City and postcode searches are submitted to a cached German place resolver; the public Nominatim endpoint is not used for keystroke autocomplete.

## Run the first application slice

The local API uses SQLite by default. Python 3.11+ and Node.js 20.19+ (or 22.12+) are required.

```bash
mise install
mise exec -- uv sync --locked
mise exec -- uv run hiring-seed
mise exec -- uv run uvicorn hiring_scraper.app.api:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The seed importer is safe to rerun. It loads 2,251 mapped Karlsruhe candidate records and the full bounded discovery snapshot. Two imports into a fresh database both produced 2,251 companies, 210 feeds and 1,664 stored job records. The current crawl parsed 42 structured feeds and 160 trusted HTML pages, yielding 1,632 rows; the imported fixture also retains eight supplemental parsed sources and 32 rows. The crawler produced a positive page/feed/provider signal for 669 of 1,606 website-bearing candidates, including 5 detection-only ATS leads. Wikidata/OSM resolution added 13 domains among 645 candidates without an OSM website tag; alongside one earlier manual domain match, 631 candidates remain without a known homepage. The default 15 km company query returns 2,249 records (1,615 with domains); the Karlsruhe-or-remote job query returns 283 active jobs (260 non-remote in-area results and 23 explicitly remote). See the [full discovery report](reports/karlsruhe-osm-full-discovery.md) and [domain/HTML follow-up](reports/full-crawl-domain-and-html-followup.md) for counts, filtering and limitations. The snapshot covers Karlsruhe; other city searches can show explicitly remote jobs, but do not yet have national company or local-job coverage.

Use **Search areas** to add a German city, state center, or postcode and choose how far around it to search. Saving a place starts the first search right away: the worker finds nearby companies from OpenStreetMap, records their locations, and looks for homepages listed directly on the map or strongly matched through linked Wikidata company records. It saves companies even when no homepage is known. Companies with a homepage then move to the existing hiring-page and job search, with both steps shown in the progress view. Saved places repeat this full search on the interval you choose. The companies and jobs pages show the results and their source links.

## Compose deployment shape

The Compose stack uses PostgreSQL/PostGIS, one-shot migration and fixture-import services, FastAPI, a recurring feed worker, a separate company-discovery worker, and a static React frontend. That worker first finds companies and websites for a location campaign, then checks website-bearing companies for hiring pages and jobs. It claims up to four companies at a time by default and crawls them concurrently; requests to the same origin remain spaced by at least one second. Set `CAREER_DISCOVERY_WORKERS` between 1 and 32 to tune concurrency. Website checks use a six-page, robots-aware crawl; company, homepage, and hiring-page progress survives API or worker restarts. The feed worker refreshes known job sources every six hours. Both workers use leases and retry scheduling. Partial job reads never close postings; closure still requires repeated complete feed scans and a grace period. GitHub Actions builds the shared API/worker image and web image in GitHub runners and publishes them to GHCR on pushes to `main` and version tags. Docker is not installed in the current development environment, so Compose startup and image publication have not been exercised here.

```bash
cp .env.example .env
# Set two different random hexadecimal values in .env.
docker compose up --build
```

Open `http://localhost:8080`. The database keeps its data in the `hiring_db` volume. Remove that volume only when you intend to discard the local database. For GHCR images, set `HIRING_API_IMAGE` and `HIRING_WEB_IMAGE` to the published image references in `.env`.

To manually process one due company, run `docker compose run --rm discovery-worker --once`. Discovery attempts, outcomes, page counts, and errors are stored in `discovery_runs`; the company's due time and lease make restarts safe. The fixture importer preserves newer worker results when a dated snapshot is imported again.

For example, on a server with package access:

```dotenv
HIRING_API_IMAGE=ghcr.io/OWNER/REPOSITORY/hiring-scraper-api:COMMIT_SHA
HIRING_WEB_IMAGE=ghcr.io/OWNER/REPOSITORY/hiring-scraper-web:COMMIT_SHA
```

Then run `docker compose pull && docker compose up -d --no-build` to use the runner-built images.

## Preview on the shared development gateway

`scripts/dev/preview.sh` builds the UI with the preview's API origin and starts the static UI, FastAPI, and a location-search worker on reserved loopback ports. The preview worker handles location campaigns and their company checks, while skipping the fixture's unrelated site-wide rescan backlog. Each slug gets its own SQLite copy and crawl captures under `/tmp/serve-dev-worktree/hiring-scraper/`; no shared database is modified. Use a fresh slug for every launch; the adapter rejects a slug whose state, database, or gateway host already exists.

```bash
slug="hiring-$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
./scripts/dev/preview.sh start --slug "$slug"
./scripts/dev/preview.sh status --slug "$slug"
./scripts/dev/preview.sh stop --slug "$slug"
```

Start runs in the foreground and cleans up only its services and gateway route when stopped or after 24 hours. It prints public links only after checking both hosts; otherwise it prints the local gateway address and required `Host` headers.

Start with [the evaluation](reports/karlsruhe-evaluation.md) and the follow-up [location-source experiments](reports/location-source-experiments.md). The current recommendation is OSM from regional extracts plus local employer directories for discovery, Arbeitsagentur for an active-hiring signal, and a bounded crawl of company homepages to verify career links.

The [implementation plan](docs/implementation-plan.md) records the source evaluation, architecture, rescan lifecycle, and later release path. The [HTML career-page extraction evaluation](reports/career-page-extraction-20.md) records earlier parser experiments; the [domain/HTML follow-up](reports/full-crawl-domain-and-html-followup.md) evaluates the full crawl and missing-domain PoC.

## Results and evidence

- [Manually checked career websites](data/karlsruhe/verified_career_sites.csv)
- [20-company IT career/feed follow-up](reports/karlsruhe-it-followup-20.md)
- [OSM candidates](data/karlsruhe/osm_candidates.csv): 1,453 mapped objects; 1,080 with website tags, not 1,453 verified companies.
- [KIT directory candidates](data/karlsruhe/kit_candidates.csv): includes employers outside Karlsruhe; filter the location column.
- [Arbeitsagentur sample](data/karlsruhe/ba_candidates.csv): one API page, not an exhaustive export.
- [Machine-readable counts](data/karlsruhe/summary.json), [12-site OSM experiment](data/karlsruhe/career_sample_results.json), and [search-assisted checks](data/karlsruhe/search_results.json).
- [15 km OSM radius candidates](data/location-sources/run-2026-10-05/osm-radius-15km-candidates.csv), [cross-source counts](data/location-sources/summary.json), and [source-by-source evaluation](reports/location-source-experiments.md).

Each HTTP request has JSON metadata containing UTC time, URL, status/error, final URL, size, and body hash. Full `.body` responses remain in this workspace but are gitignored to avoid committing entire third-party pages. A fresh clone must run the acquisition commands before offline analysis, which observes a changed web rather than reproducing the dated study. The original manual content claims cannot be independently audited from Git alone; preserve the local `.body` captures for that purpose. Search seeds were selected during interactive research; raw search result sets/rankings were not archived, and automated search access was not evaluated. Initial Leipzig preflight requests in `data/probes` predate the user's Karlsruhe selection and are excluded from the study.

## Run

Python 3.11+, standard library only. Run from the repository root.

```bash
# Makes seven bounded source/robots requests, with one-second pauses.
python3 experiments/discover.py
# Offline: recreate candidate CSVs and counts from the downloaded responses.
python3 experiments/analyze.py
# Makes robots/homepage/career requests for a fixed OSM sample (roughly 2 minutes).
python3 experiments/careers.py
# Check six manually curated search hits; this does not query a search engine.
python3 experiments/search_check.py
# Regression check for empty career navigation links.
python3 -m unittest discover -s tests -v
```

`discover.py --city ... --lat ... --lon ... --radius-m ... --out ...` changes the original BA/OSM search. The newer `osm_locations.py --city ... --radius-m ... --out ...` geocodes a German city; it also accepts `--lat`/`--lon` or `--state`. State-wide queries against public Overpass timed out in the test, so use the source evaluation for the recommended state-level approach. Choose a new output directory for every run. The directory probes, downstream scripts, curated seeds, and reports remain research fixtures rather than a nationwide production index.

## Scope

The [full Karlsruhe discovery report](reports/karlsruhe-osm-full-discovery.md) records the 2,251-candidate run and repeat-import results. This remains a dated regional snapshot rather than a nationwide index. The separate discovery worker revisits website-bearing company records and persists verified career pages, feeds, jobs and run evidence; the feed worker refreshes supported sources. Job results include postings with a work location in the selected radius **or** postings explicitly marked remote. At another city, this fixture can still show remote postings while local coverage remains empty.

Before expanding beyond Karlsruhe, add the national OSM import and broaden feed adapters. The worker runs separately from the API, uses bounded robots-aware crawls, and does not accept arbitrary user-supplied URLs. The current Wikidata enrichment is limited to the 81 identifiers already attached to the Karlsruhe OSM candidate set; it leaves 631 candidates without a known website.

## Data attribution

OSM-derived data is © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under ODbL. Preserve attribution and assess the applicable database sharing requirements when distributing derived databases. Other source data retains its source rights; this repository does not grant a license to third-party content. Successful HTTP access is an observation, not a determination of reuse permission.

## Career-page discovery POC

The follow-up crawler starts with a company homepage, follows bounded career links and sitemaps, and supports public Greenhouse, Lever, Personio, Ashby and linked Schema.org DataFeed postings. It also detects hosted Workday, SuccessFactors, Softgarden, Recruitee, Helix, Onlyfy and SmartRecruiters URLs without claiming unimplemented feeds. Run instructions and initial results are in [the POC evaluation](reports/career-discovery-poc.md); fixes and follow-up experiments are in [the improvements report](reports/career-discovery-improvements.md).

Example:

```bash
python3 -m hiring_scraper --seeds data/career-poc/seeds-full-validation.json --out data/career-poc/my-run
```

For broader checks, `--workers 4` runs several companies concurrently while sharing a per-origin one-second pacer and one total live-request budget. It checkpoints results during the run; the default is one worker.

Runs require a new output directory. The input seed lists and result manifests make the scope visible. Body captures stay local and are omitted from Git; reuse them with `--cache-from` for deterministic parsing experiments.

## Profile matching PoC

Open **Your profile** to enter skills and preferred roles or suggest skills from pasted CV text. Review the suggestions before saving; CV text is not persisted or sent to an external AI provider. Select a saved profile in the jobs view to rank and filter results. Scores measure skill/role overlap; job details show source excerpts, preference conflicts and information gaps. Incomplete jobs stay included by default. The location-plus-remote rule still applies.

Job enrichment runs during imports and both crawl workers. A separate bounded detail worker can improve sparse descriptions using verified structured data or scoped role HTML:

```bash
python -m hiring_scraper.app.enrichment --fetch-details --limit 20
# Optional deployment service; runs in the existing API image:
docker compose --profile enrichment up -d
```

See the [evaluation and repeatable PoC](reports/profile-matching-poc.md) for coverage, matching limitations, deployment commands and next steps. Profiles currently belong to the shared app workspace; multi-user ownership and PDF/DOCX import are not implemented.

## CV-based Heidelberg board

The [CV board findings](docs/cv-profile-board-findings.md) describe the supplied profile, independent cheaper-model audits, ranking comparisons and coverage gaps. The sanitized profile in `fixtures/cv_profile.json` omits the applicant's name, contact details and CV text. It distinguishes professional work from academic/research CAD and prototyping, records language proficiency, and uses an editable Heidelberg 35 km search area. Its broader default score threshold is 20; scores indicate evidence overlap, not hiring probability.

The ordinary seed does not import this profile or regional captures. Import them explicitly into your selected development database:

```bash
mise exec -- uv run python -m hiring_scraper.app.cv_board \
  --profile fixtures/cv_profile.json \
  --regional-data data/local-cv-board/ba-details.json \
  --employer-data data/local-cv-board/employer-supplement.json
```

Full job descriptions and source responses stay in ignored local files. For a new bounded Arbeitsagentur acquisition, choose a fresh output directory. This uses the observed public Jobsuche endpoint, whose interface is not a guaranteed stable contract:

```bash
mise exec -- uv run python -m hiring_scraper.app.regional acquire \
  --profile-json fixtures/cv_profile.json --out data/local-cv-board/refresh-new \
  --max-pages 3 --max-details 300 --max-requests 500 --import
```

Check the run manifest for truncation, failed details and request-budget exhaustion. Regional feeds are refreshed through this explicit command; ordinary ATS refreshes do not schedule them. Missing jobs in a partial search never imply closure. The board excludes expired postings, jobs outside the commute area, and explicitly incompatible remote countries before pagination; unknown remote country permission stays a flagged possible lead.

The acquisition writes `snapshot.json` in its output directory. For a fresh installation, supply that file to the profile importer as `--regional-data`; `--employer-data` is optional.

To seed an isolated preview, set `HIRING_PREVIEW_PROFILE`, `HIRING_PREVIEW_REGIONAL_SNAPSHOT`, and optionally `HIRING_PREVIEW_EMPLOYER_SNAPSHOT` to those file paths before starting the documented preview adapter with a fresh slug. Saved-profile links carry the profile area and filters. Language levels, evidence notes and the search area can be edited in **Your profile**.

Reproduce matching comparisons using the original local text captures and frozen judgments:

```bash
mise exec -- uv run python experiments/cv_profile_evaluation.py \
  --jobs data/local-cv-board/evaluation/expanded-heldout-input.json \
  --labels experiments/fixtures/cv_job_labels/expanded-heldout.json \
  --profile fixtures/cv_profile.json --threshold 20 --output /tmp/cv-evaluation.json
node tests/frontend_profile_defaults.mjs
```

Judgment text hashes reject changed inputs. A fresh acquisition observes a changed market and cannot reproduce the dated measurements without the original local captures. Reported recall applies to the judged samples, not every vacancy in the region. The board remains a shared workspace without per-user access control.
