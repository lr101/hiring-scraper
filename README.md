# Radius

**Goal: find jobs, match them to your profile, and make applying easier, stress-free
and gamified.**

1. **Find** — discover German employers and keep their public job postings current.
2. **Match** — rank those jobs against your profile with explainable evidence, so
   good fits rise and poor fits stay out of the way.
3. **Apply** — prepare tailored documents, fill forms, and track every application
   through a Journey with weekly goals and momentum, so progress feels rewarding
   rather than stressful.

Radius runs on PostgreSQL with PostGIS and pgvector, in development, tests and
production alike. Compose starts without fixture data; when background workers are
enabled, it seeds the configured public RSS feeds. See the
[architecture overview](docs/architecture.md) for how the pieces fit together.

## Local development

Requires Python 3.11+, Node.js 22.13+, [mise](https://mise.jdx.dev/), and either
Docker or a local PostgreSQL 17 with PostGIS and pgvector.

Start the database (the project's image, published on `127.0.0.1:5432`):

```bash
cp .env.example .env   # set POSTGRES_PASSWORD; see Compose deployment
docker compose -f compose.yaml -f compose.dev.yaml up -d db
export DATABASE_URL="postgresql+psycopg://postgres:<POSTGRES_PASSWORD>@127.0.0.1:5432/hiring"
```

Then migrate, seed the Karlsruhe fixture and start the API:

```bash
mise install
mise exec -- uv sync --locked
mise exec -- uv run python scripts/dev/migrate.py
mise exec -- uv run hiring-seed
mise exec -- uv run uvicorn hiring_scraper.app.api:app --reload
```

`scripts/dev/migrate.py` runs the Alembic migrations plus the rollback-safe step the
container entrypoint applies, so the local schema matches a deployment.

Background work runs in workers, as in production. Start each in its own terminal
(all use the same `DATABASE_URL`):

```bash
mise exec -- uv run python -m hiring_scraper.app.worker                  # feed scans
mise exec -- uv run python -m hiring_scraper.app.search_snapshot_worker  # browse results
mise exec -- uv run python -m hiring_scraper.app.profile_match_worker    # Journey matching
mise exec -- uv run python -m hiring_scraper.app.application_preparation_worker
```

Requests never match or build search results synchronously. While the search worker
rebuilds, the API serves the previous snapshot and the web app refreshes when the new
one is ready. Journey returns saved roles immediately and reports pending matches until
the profile match worker has caught up; the page checks again automatically.

Migration `0033_application_deduplication` consolidates existing duplicate Journey
applications per profile using the same vacancy identity rules as search. It
keeps the selected application and archives removed stages, undo state, and
timestamps in `job_application_deduplication_archive`. Source job records and
momentum events stay intact. Downgrading restores archived entries that have no
newer saved state. Background matching also retracts redundant automatic New entries
after each publication.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>.

Save a job profile under **Your profile**.

Save a PNG portrait up to 2 MiB and 4096 pixels per side under
**Edit profile → CVs & writing → CV photo (optional)**. The CV editor and local
PDF renderer use this portrait for CVs with a portrait placeholder.

Viewing a job detail adds it to
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

**Today's focus** at the top of Journey keeps the day small: up to three roles to act on
now. Roles with prepared documents come first and open straight into review, then
roles you marked Interested, then your strongest matches. Three dots fill as you send
applications (marking a role Waiting for reply or Automated application). After three,
the day is done and anything more is a bonus. Closed or expired postings are skipped,
and nothing is sent on your behalf.

Profile matching reads each posting with a small local language model and checks
it against your saved career facts with a local reranker. The model lists the
posting's duties and requirements as verbatim quotes and names its occupational
field and industry; benefits, company descriptions and application instructions
are never treated as requirements. For every duty and requirement the reranker
then reads the closest lines of your CV together with the job's field and decides
whether they actually show it, so a shared word in an unrelated field (for example
"Schulungen" in a SaaS support role versus training in corrosion protection) does
not count. Everything runs on CPU in separate containers; there are no API calls or
subscription quotas. See [docs/local-models.md](docs/local-models.md).

The job page shows the posting's field, industry, duties, must-haves and
nice-to-haves under **What this job asks for**. Quotes come from the posting,
and the section is available without a profile or a match score. It shows
**Reading this posting…** while the first analysis is queued and keeps the
last usable analysis visible during a refresh.

The score (0–100) adds duty evidence, mandatory and preferred requirements, role
fit and occupational-field fit, then subtracts bounded penalties (work style,
employment type, unverified formal prerequisites, and at most two points for
language or work-permit flags) and applies a calibration fitted on the DEV split of
the evaluation set. The job page shows how the score was built: earned against
possible points per part, deductions, and a checklist of the posting's duties and
requirements with the CV line that supports each. Only explicit incompatibilities cited from both the job and the
profile hide a job.

Jobs the models have not analysed yet show **Analysing job…** and stay out of
Journey until they are done; jobs in a profile's local search area are analysed
first. To keep the local LLM's throughput focused on likely matches when
covering all of Germany, automatic analysis applies a two-stage gate:

- **Age cutoff** (`RADIUS_ANALYSIS_MAX_AGE_DAYS`, default `14`): only postings
  published within the window (falling back to `first_seen_at` when `date_posted`
  is not an ISO date) enter or remain eligible in the automatic queue.
- **Embedding pre-rank** (`RADIUS_ANALYSIS_MIN_SIMILARITY`, default `0.80`):
  queued postings are scored in bounded SQL batches by cosine similarity between
  the posting's `intfloat/multilingual-e5-small` embedding and each profile's
  embedding. `0.80` matches the lower relevance band of `multilingual-e5-small`
  (whose cosine similarities sit roughly in `0.75–0.93`, with unrelated postings
  typically below `0.80`). Postings below the threshold are skipped, while
  eligible postings run in descending similarity order (still preferring in-area
  roles when a profile has a local radius). Postings waiting for their first
  embedding wait without blocking the queue.
- **User-intent override**: postings you save, move to any non-automatic Journey
  stage, or open on the job page are queued with priority and analysed regardless
  of age or similarity.

**Settings** shows **Local matching progress** for gated postings, profile
matches, last-hour throughput, ETA, and a muted count of postings skipped due to
age or low similarity. After a job or CV edit, the previous result stays visible
(marked as updating) until the new analysis replaces it, and gate changes
(profile edits, new profiles, or threshold updates) re-score eligible postings
automatically. Results are stored per profile/job in `profile_job_matches`; the
profile match worker rescores from stored analyses and evidence and never runs
inference itself.

Explicit CV language sections can supply a missing language level without
replacing a saved level; absence of a language claim remains unknown.
Country-agnostic authorization flags are not country-specific eligibility evidence.

For local development, run the models and both workers:

```bash
scripts/dev/local-models.sh start
mise exec -- uv run python -m hiring_scraper.app.semantic_search_worker
mise exec -- uv run python -m hiring_scraper.app.job_analysis_worker
```

For a preview of the profile matching worker, set `HIRING_PREVIEW_PROFILE_MATCHING=true`
with a fresh preview slug; `HIRING_PREVIEW_LOCAL_SEMANTICS=true` also starts the local
embedding service.

### Application preparation

On a job's application page, confirm the imported base CV in the chosen language.
Choose **Prepare documents** to draft a tailored CV and cover letter through the
connected ChatGPT subscription. **Full** adds a review pass, while **Fast** drafts
the pair. Edit the documents in place, step through AI changes with word diffs,
source citations and findings in the inline review panel (accepting or restoring
wording as you go), and download each saved version as a PDF. Radius stores up to
20 versions per document and the applicant submits the application. See the
[application preparation guide](docs/application-preparation.md).

### City election context

German city and municipality records show the AfD share of valid second votes in
the 2025 Bundestag election. This is an aggregate result from the latest federal
election, not a live poll or a measure of any individual resident. The source is
the Federal Returning Officer's [2025 polling-district CSV release](https://www.bundeswahlleiterin.de/mitteilungen/bundestagswahlen/2025/20250430_wbz_btw25.html).
The checked-in municipality file is derived from its published CSV using
[`scripts/import_btw2025_municipality_results.py`](scripts/import_btw2025_municipality_results.py).

Profile settings can optionally add or deduct up to 20 raw points before v2
calibration based on this share. A value of 0 disables the adjustment; a positive setting raises
scores below 25% and lowers scores above 25%, capped at the selected value. Only
unambiguous municipality-name matches are used, so missing or ambiguous city
data leaves the match unchanged. Job details show the leading party's vote share
and city in a dedicated overview row.
Some postal ballots were counted for a
shared local authority and cannot be assigned to one municipality; those
polling-place-only figures are labelled and never affect scores. Fully remote
jobs are not adjusted by a city result.

## Compose deployment

Compose pulls the app and PostgreSQL/PostGIS image with pgvector from GitHub Container Registry (GHCR). The deployment host needs `compose.yaml`, `.env`, and `docker/chromium-seccomp.json`; the app image includes the frontend, migrations, the pinned local multilingual embedding model, and startup logic. A background worker keeps company/job vectors current and prebuilds profile-area job snapshots, so normal job search does not wait for profile-match rebuilds. The one-shot `migrate` service prepares the database role and applies migrations before the API and workers start. The API and each worker run in separate containers. When background workers are enabled, the feed worker adds the configured default RSS sources from `hiring_scraper/app/default_job_feeds.json` and queues their first scans; it does not import fixture companies or jobs. Open **Search areas** in the app and add a location to create the first company discovery job. PostgreSQL data persists in a named volume.

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
docker compose pull
docker compose up -d --no-build
```

If startup is already failing with `extension "vector" is not available`, pull the published database image and recreate the database and app containers:

```bash
docker compose pull db
docker compose up -d --no-build --force-recreate
```

This keeps the named database volume. Once PostgreSQL is healthy, the migration service runs against the updated image.

When upgrading from the single-container layout, stop the old app before starting the new release so the old workers do not overlap the new ones. Existing preparation drafts remain on the Codex volume. New PostgreSQL preparation jobs are persisted and resume from checkpoints after an interrupted worker lease expires.

The first app image pull downloads the pinned ONNX model (about 235 MB); the `llm` and `llm-reranker` containers download their pinned GGUF weights (about 3.4 GB) into the `llm_models` volume on first start, and the job analysis worker waits for them. The search worker then indexes existing companies and jobs in the background; newly changed records are queued as they are discovered or edited. The jobs page can serve a recent snapshot while the next refresh runs, and text queries use local semantic vectors once indexed.

Open <http://localhost:8080> (or the `APP_PORT` you set in `.env`). `docker compose down` preserves the database volume; use `docker compose down --volumes` to erase it. For a pinned deployment, set `HIRING_APP_IMAGE` and `HIRING_DB_IMAGE` in `.env` to the tags from the same commit (`COMMIT_SHA` for the app and `db-COMMIT_SHA` for the database), or pin each image to its own digest. Then run `docker compose pull && docker compose up -d --no-build`.

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
postings whose work location is in Germany, plus remote roles open to Germany.
The work location decides, not the employer: a German company's role in Romania
is not listed. A posting that states no work location at all is kept when its
employer is in Germany (or the state) and flagged as location unknown; Journey
adds such postings for profiles in the employer's country the same way. The company list still uses employer membership (`country=DE`,
including legacy companies with an observed German state); the `country_code`
parameter still describes remote eligibility in radius searches.
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
Deployments apply the state-search migration during startup. Existing companies gain state membership
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
website request budget is exhausted.

## Documentation

- [Architecture](docs/architecture.md)
- [Saved CVs, structured application documents and PDF export](docs/application-preparation.md)
- [Parallel application stages](docs/application-stages.md)
- [CV formatting prompt](docs/cv-formatting-prompt.md)
- [Matching strategy](docs/matching-strategy-review.md)
- [German job-source candidates](docs/job-source-candidates.md)
- [Chrome application companion](extensions/application-companion/README.md)
- [Demo application profile](fixtures/demo-application/README.md)
- [CI and release guide](docs/ci.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Maintainer checklist](docs/maintaining.md)

Research prototypes, benchmark captures and trial reports from the proof-of-concept
phase were removed from the tree; they remain available in Git history up to
commit `0f89adca`.

## License

The software is licensed under the [MIT License](LICENSE). Third-party data retains
its respective licenses and attribution requirements.

The Karlsruhe seed fixture includes OpenStreetMap-derived data. See [OpenStreetMap attribution and license](https://www.openstreetmap.org/copyright); other source data retains its respective rights.

## Chrome application companion

In **Settings → Application companion**, download the extension ZIP and load its
`extension/` folder unpacked in Chrome. Connect the extension to Radius and select
your profile. Use **Prepare documents** to open the Radius editor for a job.
The companion can capture employer form questions and fill saved profile facts
when you choose to fill. Attach PDFs downloaded from Radius on the employer's form.

### German and English application profiles

The profile editor and companion offer **English** and **Deutsch** tabs for base CVs,
summaries, writing instructions, career evidence, writing samples, goals, skill and
qualification wording, and common application wording. Each version is saved
independently; switching tabs preserves unsaved edits. Existing profile text is
available in the English tab. Fill the German tab with your reviewed German wording.
Missing German text stays blank rather than silently borrowing English prose.

Contact details, salary amounts and currency, availability dates, weekly hours,
work permissions, search area and board settings are shared outside the tabs.
Application preparation uses the selected base CV and matching profile language.
Connected companion saves and syncs preserve both language versions.
