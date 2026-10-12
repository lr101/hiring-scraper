# Radius

Radius discovers employers and job openings in Germany. Local development uses SQLite; Compose runs PostgreSQL/PostGIS and starts without fixture data. When background workers are enabled, it seeds the configured public RSS feeds.

## Local development

Requires Python 3.11+, Node.js 20.19+ (or 22.12+), and [mise](https://mise.jdx.dev/).

```bash
mise install
mise exec -- uv sync --locked
mise exec -- uv run hiring-seed
mise exec -- uv run uvicorn hiring_scraper.app.api:app --reload
```

To scan the configured default RSS sources locally, start the feed worker in a
separate terminal:

```bash
mise exec -- uv run python -m hiring_scraper.app.worker
```

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>.

Save a job profile under **Your profile**, then choose an application status on a
job in the results or job details. **My applications** shows tracked postings for
each profile, with counts and filters for New, Open, Not interested, Waiting for
reply, Interview, Rejected, and Accepted. Statuses persist independently of the
source posting; closed and expired postings remain in your application overview.

## Compose deployment

Compose runs two containers: the app and PostgreSQL/PostGIS. It pulls the public app image from GHCR by default. The deployment host only needs `compose.yaml` and `.env`; the app image includes the frontend, migrations, optional fixture data, and startup logic. On startup, the app prepares its database role and applies migrations. When background workers are enabled, the feed worker adds the configured default RSS sources from `hiring_scraper/app/default_job_feeds.json` and queues their first scans; it does not import fixture companies or jobs. Open **Search areas** in the app and add a location to create the first company discovery job. PostgreSQL data persists in a named volume.

Copy `compose.yaml` and `.env.example` to the deployment host as `compose.yaml` and `.env`. Replace both placeholder passwords with different random hex values, then protect the file before starting:

```bash
cp .env.example .env
# Replace both placeholder passwords with different values from: openssl rand -hex 32
chmod 600 .env
docker compose pull
docker compose up -d
```

Open <http://localhost:8080> (or the `APP_PORT` you set in `.env`). `docker compose down` preserves the database volume; use `docker compose down --volumes` to erase it. For a pinned deployment, set `HIRING_APP_IMAGE` in `.env` to a full commit tag or image digest, then run `docker compose pull app && docker compose up -d`.

To discover employers across Germany, open **Search areas** and use the default
**All of Germany** coverage. Discovery advances through small map sections across
all 16 states, saving each section and checking discovered employers while the
inventory continues. Public listings are incomplete, so this is observed coverage,
not a complete business register.

Germany runs continuous ranked checks for both career pages and saved job feeds.
Employers with at least 250 employees or recent jobs are checked daily; smaller
active employers every 3 days; unknown employers every 7 days; repeatedly empty
successful checks every 30 days. Repeatedly irrelevant employers can be paused.
Checks are spread over time. Errors do not establish irrelevance. Company details
show the cadence, reason, and next check. A manual Germany inventory preserves an
employer's existing rank and pause state; fresh size or hiring evidence can reactivate
a parked employer. Employers without a mapped homepage are considered in a separate,
rate-limited verification queue when public contact evidence or configured search is
available. They remain listed if no website can be verified, but cannot receive job
checks until one is found.

Use **View companies and jobs** on the Germany card to browse active, unexpired
postings from German employers across all work locations, including remote and
unspecified locations. The API uses `country=DE` for employer membership; the
existing `country_code` parameter still describes remote eligibility in radius
searches. Legacy companies with an observed canonical German state are included.

To search a whole German state, open **Search areas**, choose **Whole German state**
under **Search coverage**, and select a state such as Baden-Württemberg. The search
uses the state's administrative boundary to find publicly mapped named offices,
craft businesses, and industrial employers, then checks every discovered company
with an available website for hiring sources. State searches query smaller map sections, subdivide sections that time out, and
retry temporary service failures. Progress shows completed and remaining sections.
These searches can take longer than city searches. Companies without a website are still saved; missing-homepage
verification retains its configured request/search budgets. Public listings are
not a complete business register.

Use **View companies and jobs** on the saved state card to browse the results.
State results include active, unexpired postings from employers discovered in
that state, including jobs elsewhere, jobs without a work location, and remote
roles. Choose **All jobs** under the profile filter for an unfiltered employer
view. City/radius searches continue to filter by the job's work location.
PostgreSQL deployments apply the state-search migration during startup; existing
local SQLite databases upgrade additively. Existing companies gain state membership
when a state discovery observes them, rather than guessing it from their address.

Scheduled refreshes revisit saved job feeds and career listing pages to find new postings. Recurring search-area schedules queue these known sources without repeating company, homepage, or career-page discovery. Feed failures retry the same saved URL with backoff. Use a manual discovery job to find additional companies or rediscover their career sources.

Use **Job feeds** in the navigation to add and manage public HTTPS job pages and RSS / Atom feeds. Add a page to an existing company from its detail page, or leave the optional company/source name blank to derive a label from the host. Choose **ATS career page · auto-detect** for a supported Greenhouse, Lever, Personio, Ashby, Recruitee, or SmartRecruiters board; the app saves its public provider feed. Choose **Public HTML page** for server-rendered listings, **JavaScript page · Chromium** when listings appear only after scripts run, or **RSS / Atom feed** for a publisher-provided feed. Choose **Agentur für Arbeit · scheduled search** to add the BA Jobsuche adapter with optional job terms. Its search scope follows scheduled discovery: a Germany-wide scan has no location parameters, while a city/radius scan uses that saved area. Other sources can also use an optional location filter and radius. Feeds can be paused, edited, and scanned on demand. Rolling or geographically filtered feeds are treated as incomplete, so an omitted item will not close a previously seen job. A bot or CAPTCHA challenge is saved as the scan reason and stops that scan. See the [German job-source guide](docs/job-source-candidates.md) for tested providers and search details.

The Docker image includes headless Chromium. For local development, install it once with `mise exec -- uv run playwright install --only-shell chromium`. Headless rendering executes page JavaScript. The scanner does not solve CAPTCHAs, bypass challenges, sign in, or reuse human session cookies. See the [candidate source list](docs/job-source-candidates.md) for pages to try and current limitations.

Public career discovery, website verification, feed refresh, and detail enrichment
do not request or enforce robots.txt. Requests retain the identifying user agent,
public-IP checks, bounded redirects, request caps, and per-origin pacing. HTTP
429/503 pauses that origin, honoring `Retry-After`, while other origins can
continue. A bot or CAPTCHA challenge stops the affected scan and is recorded as
`bot_challenge`; the scanner does not attempt to solve it.

Career discovery now defaults to 24 pages, depth 5, and 48 requests per company.
The page limit is configurable up to 24.
The [follow-up Bonn public-source trial](reports/bonn-public-discovery.md) compares
six-, twelve-, and twenty-four-page results and distinguishes group-wide rows
from verified employer vacancies.

Company discovery fills missing websites from linked Wikidata entries and public,
identity-verified email domains. Set `TAVILY_API_KEY` in `.env` to enable an
additional company-name and location search fallback. Tavily uses basic searches
and takes precedence over the optional `BRAVE_SEARCH_API_KEY` alternative. For
local development, start Uvicorn with `--env-file .env` to load the key. Search results are checked
against current company identity before acceptance. Unresolved companies get basic
contact/imprint and official-website query fallbacks. Searches without source
locality use the nearest mapped city's name as a regional hint; acceptance still
requires source identity evidence. Verification checks up to four same-host pages,
prefers current imprint/contact evidence, and rejects conflicting legal owners or
ambiguous domains. Run a manual discovery job on an existing search area to
fill missing websites on saved companies. Each job defaults to 100 missing companies,
50 searches, and 180 website requests, configurable
with `HOMEPAGE_DISCOVERY_MAX_COMPANIES`, `HOMEPAGE_DISCOVERY_MAX_SEARCHES`, and
`HOMEPAGE_DISCOVERY_MAX_REQUESTS`. Each query pass reserves a third of its remaining
search allowance for later fallbacks, within the same total budget; consequently,
large batches may give fewer companies an initial search. Search stops when the
website request budget is exhausted. See the [website discovery experiment](reports/website-discovery-poc.md)
for measured results and limitations.

The [expanded 120-record homepage benchmark](reports/expanded-homepage-discovery-poc.md)
compares query fallbacks, page verification, directory links, structured data,
and paired basic/advanced Tavily searches. Recompute its audited results without
network access using `.venv/bin/python -m experiments.summarize_expanded_homepages`.

## Documentation

- [Architecture and implementation plan](docs/implementation-plan.md)
- [CV profile board findings](docs/cv-profile-board-findings.md)
- [POC review and proposed accuracy experiments](reports/poc-review-and-next-experiments.md)
- [Production improvement trials and retained changes](reports/production-improvement-trials.md)
- [Bonn 102-employer discovery benchmark and improvements](reports/bonn-discovery-benchmark.md)
- [Bonn public-source follow-up, rate limits, and retained adapters](reports/bonn-public-discovery.md)
- [CI and release guide](docs/ci.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Maintainer checklist](docs/maintaining.md)
- [Research reports](reports/)

## License

The software is licensed under the [MIT License](LICENSE). Third-party data retains
its respective licenses and attribution requirements.

The Karlsruhe sample includes OpenStreetMap-derived data. See [OpenStreetMap attribution and license](https://www.openstreetmap.org/copyright); other source data retains its respective rights.
