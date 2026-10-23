# Radius

Radius discovers employers and job openings in Germany. Local development uses SQLite; Compose runs PostgreSQL/PostGIS and starts without fixture data. When background workers are enabled, it seeds the configured public RSS feeds.

## Local development

Requires Python 3.11+, Node.js 22.13+, and [mise](https://mise.jdx.dev/).

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

Start the search snapshot worker in another terminal to refresh cached browse
results after new jobs arrive. While it rebuilds, the API serves the previous
profileless snapshot and the web app refreshes it when the new snapshot is ready:

```bash
mise exec -- uv run python -m hiring_scraper.app.search_snapshot_worker
```

PostgreSQL deployments also run a profile matching worker. Journey returns saved
roles immediately while this worker rebuilds stale matches after a profile edit
or matcher update. The page checks for completed matches automatically. Compose
starts the worker when `RUN_BACKGROUND_WORKERS=true`; when running PostgreSQL
without Compose, start it separately:

```bash
mise exec -- uv run python -m hiring_scraper.app.profile_match_worker
```

SQLite development keeps the existing synchronous matching flow.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>.

Save a job profile under **Your profile**. Viewing a job detail adds it to
**My applications** as New. Opening the original post leaves its stage unchanged.
Mark a role as Interested
when you choose to pursue it; you can also move it to Not interested later.
Update stages from the job details or application overview.
**In GOD's hand** is an alternative to Interested for overqualified roles and can
be selected from New or Interested. **Automated application** runs alongside
Waiting for reply for applications sent using browser-plugin autofill. Both sent
stages count toward application momentum; filling a form alone does not.
Statuses are saved per profile and remain visible after a source posting closes
or expires.

### City election context

German city and municipality records show the AfD share of valid second votes in
the 2025 Bundestag election. This is an aggregate result from the latest federal
election, not a live poll or a measure of any individual resident. The source is
the Federal Returning Officer's [2025 polling-district CSV release](https://www.bundeswahlleiterin.de/mitteilungen/bundestagswahlen/2025/20250430_wbz_btw25.html).
The checked-in municipality file is derived from its published CSV using
[`scripts/import_btw2025_municipality_results.py`](scripts/import_btw2025_municipality_results.py).

Profile settings can optionally adjust a match score by up to 20 points based
on this share. A value of 0 disables the adjustment; a positive setting raises
scores below 25% and lowers scores above 25%, capped at the selected value. Only
unambiguous municipality-name matches are used, so missing or ambiguous city
data leaves the match unchanged. Job details show the leading party's vote share
and city in a dedicated overview row.
Some postal ballots were counted for a
shared local authority and cannot be assigned to one municipality; those
polling-place-only figures are labelled and never affect scores. Fully remote
jobs are not adjusted by a city result.

## Compose deployment

Compose pulls the app and PostgreSQL/PostGIS image with pgvector from GitHub Container Registry (GHCR). The deployment host needs `compose.yaml`, `.env`, and `docker/chromium-seccomp.json`; the app image includes the frontend, migrations, the pinned local multilingual embedding model, and startup logic. A background worker keeps company/job vectors current and prebuilds profile-area job snapshots, so normal job search does not wait for profile-match rebuilds. On startup, the app prepares its database role and applies migrations. When background workers are enabled, the feed worker adds the configured default RSS sources from `hiring_scraper/app/default_job_feeds.json` and queues their first scans; it does not import fixture companies or jobs. Open **Search areas** in the app and add a location to create the first company discovery job. PostgreSQL data persists in a named volume.

Copy `compose.yaml`, `.env.example`, and `docker/chromium-seccomp.json` to the deployment host, preserving the `docker/` path. On Ubuntu 24.04+, install the included AppArmor profile so the host can selectively allow Chromium's sandbox user namespace:

```bash
sudo install -m 0644 docker/chromium.apparmor /etc/apparmor.d/hiring-scraper-chromium
sudo apparmor_parser -r /etc/apparmor.d/hiring-scraper-chromium
```

Set `HIRING_APPARMOR_PROFILE=hiring-scraper-chromium` in `.env` on that host. Replace both placeholder passwords with different random hex values, then protect the file before starting:

```bash
cp .env.example .env
# Replace both placeholder passwords with different values from: openssl rand -hex 32
chmod 600 .env
docker compose pull app db
docker compose up -d --no-build
```

If startup is already failing with `extension "vector" is not available`, pull the published database image and recreate the database and app containers:

```bash
docker compose pull db
docker compose up -d --no-build --force-recreate db app
```

This keeps the named database volume. Once PostgreSQL is healthy, the app retries its migrations against the updated image.

The first app image pull downloads the pinned ONNX model (about 235 MB). The search worker then indexes existing companies and jobs in the background; newly changed records are queued as they are discovered or edited. The jobs page can serve a recent snapshot while the next refresh runs, and text queries use local semantic vectors once indexed.

Open <http://localhost:8080> (or the `APP_PORT` you set in `.env`). `docker compose down` preserves the database volume; use `docker compose down --volumes` to erase it. For a pinned deployment, set `HIRING_APP_IMAGE` and `HIRING_DB_IMAGE` in `.env` to the tags from the same commit (`COMMIT_SHA` for the app and `db-COMMIT_SHA` for the database), or pin each image to its own digest. Then run `docker compose pull app db && docker compose up -d --no-build`.

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
Job search uses text relevance rather than profile-match filtering. The selected
profile supplies its saved area and application-tracking context.

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
roles. City/radius searches continue to filter by the job's work location.
PostgreSQL deployments apply the state-search migration during startup; existing
local SQLite databases upgrade additively. Existing companies gain state membership
when a state discovery observes them, rather than guessing it from their address.

Scheduled refreshes revisit saved job feeds and career listing pages to find new postings. Recurring search-area schedules queue these known sources without repeating company, homepage, or career-page discovery. Feed failures retry the same saved URL with backoff. Use a manual discovery job to find additional companies or rediscover their career sources.

Use **Job feeds** in the navigation to add and manage public HTTPS job pages and RSS / Atom feeds. Add a page to an existing company from its detail page, or leave the optional company/source name blank to derive a label from the host. Choose **ATS career page · auto-detect** for a supported Greenhouse, Lever, Personio, Ashby, Recruitee, or SmartRecruiters board; the app saves its public provider feed. Choose **Public HTML page** for server-rendered listings, **JavaScript page · Chromium** when listings appear only after scripts run, or **RSS / Atom feed** for a publisher-provided feed. Choose **Agentur für Arbeit · scheduled search** to add the BA Jobsuche adapter with optional job terms. Its search scope follows scheduled discovery: a Germany-wide scan has no location parameters, while a city/radius scan uses that saved area. Other sources can also use an optional location filter and radius. Feeds can be paused, edited, and scanned on demand. A successful complete scan automatically closes previously seen jobs that are no longer listed, removing them from active job results. Closed postings remain stored with their closure date and can be found using the **Posting: Open / Closed / All** search filter; the default is Open. In Journey, Accepted and Rejected stay unchanged, Waiting for reply and Automated application move to Rejected, and all other stages move to Closed under Past outcomes. Reappearing postings reopen the same saved job. Failed scans leave existing jobs unchanged. Rolling or geographically filtered feeds are treated as incomplete, so an omitted item will not close a previously seen job. A bot or CAPTCHA challenge is saved as the scan reason and stops that scan. See the [German job-source guide](docs/job-source-candidates.md) for tested providers and search details.

The Docker image includes headless Chromium. For local development, install it once with `mise exec -- uv run playwright install --only-shell chromium`. Headless rendering executes page JavaScript. Ubuntu 24.04+ deployments use the included AppArmor profile instead of changing the host-wide user-namespace policy. The custom seccomp profile is based on [Moby's default profile](https://github.com/moby/moby/blob/f40e9e46a30434c87dc3505dfcd2d605d6b55466/vendor/github.com/moby/profiles/seccomp/default.json) and adds only namespace calls gated on `CLONE_NEWUSER`; Chromium's own sandbox remains enabled. The scanner does not solve CAPTCHAs, bypass challenges, sign in, or reuse human session cookies. See the [candidate source list](docs/job-source-candidates.md) for pages to try and current limitations.

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

- [Saved CVs, application handoff and original-template document export](docs/application-handoff.md)
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

## Chrome application companion

In **Settings → Application companion**, download the extension ZIP and load its `extension/` folder unpacked in Chrome. Save your reusable details, connect Radius, then open a job’s **Start application** flow to tailor its CV with ChatGPT. Saved profile CVs retain their original DOCX template; profiles without an attached CV can save CV text in the browser or attach one to the profile. The toolbar shows prepared jobs and follows their Apply links to employer forms. Filling is user-triggered and never submits an application or changes its tracked stage.

[Installation, privacy, limits, and developer checks](extensions/application-companion/README.md). CI builds the same installable ZIP and checksum for every PR/main/tag, and includes them in tagged releases.

### German and English application profiles

The profile editor and companion offer **English** and **Deutsch** tabs for base CVs,
summaries, writing instructions, career evidence, writing samples, goals, skill and
qualification wording, and common application wording. Each version is saved
independently; switching tabs preserves unsaved edits. Existing profile text is
available in the English tab. Fill the German tab with your reviewed German wording.
Missing German text stays blank rather than silently borrowing English prose.

Contact details, salary amounts and currency, availability dates, weekly hours,
work permissions, search area and board settings are shared outside the tabs.
Application and fit briefs select the matching profile language alongside the CV.
German briefs use German drafting and LaTeX instructions; JSON keys and history
IDs retain the same machine-readable format in both languages. Connected companion
saves and syncs preserve both language versions.
