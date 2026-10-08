# Radius

Radius discovers employers and job openings in Germany. Local development uses SQLite; Compose runs PostgreSQL/PostGIS and starts with an empty database.

## Local development

Requires Python 3.11+, Node.js 20.19+ (or 22.12+), and [mise](https://mise.jdx.dev/).

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

Open <http://localhost:5173>.

## Compose deployment

Compose runs two containers: the app and PostgreSQL/PostGIS. It pulls the public app image from GHCR by default. The deployment host only needs `compose.yaml` and `.env`; the app image includes the frontend, migrations, optional fixture data, and startup logic. On startup, the app prepares its database role, applies migrations, and serves the UI, API, and idle background workers without adding companies, feeds, or jobs. Open **Search areas** in the app and add a location to create the first discovery job. PostgreSQL data persists in a named volume.

Copy `compose.yaml` and `.env.example` to the deployment host as `compose.yaml` and `.env`. Replace both placeholder passwords with different random hex values, then protect the file before starting:

```bash
cp .env.example .env
# Replace both placeholder passwords with different values from: openssl rand -hex 32
chmod 600 .env
docker compose pull
docker compose up -d
```

Open <http://localhost:8080> (or the `APP_PORT` you set in `.env`). `docker compose down` preserves the database volume; use `docker compose down --volumes` to erase it. For a pinned deployment, set `HIRING_APP_IMAGE` in `.env` to a full commit tag or image digest, then run `docker compose pull app && docker compose up -d`.

Scheduled refreshes revisit saved job feeds and career listing pages to find new postings. Recurring search-area schedules queue these known sources without repeating company, homepage, or career-page discovery. Feed failures retry the same saved URL with backoff. Use a manual discovery job to find additional companies or rediscover their career sources.

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
50 searches, and 180 website requests (including robots and redirects), configurable
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
