# Hiring Scraper

A private, self-hosted monitor for jobs published on German company career sites.

## Development

This repository uses mise and `uv`. Docker is not required inside the provided development
container.

```bash
mise trust
mise run setup
mise run test
```

See [AGENTS.md](AGENTS.md) for the commands used by coding agents.

## Dynamic company and location discovery

The setup flow has two separate questions:

1. Create one or more job profiles describing the roles and skills to match. Use one title per line
   or separate titles with commas. For required skills, use one required group per line and commas
   for alternatives within a group. For example, `Python, Django` followed by `AWS, Azure` means
   Python and Django are required, plus either AWS or Azure.
2. Open **Where to look** and add company websites or city searches. Every enabled profile in the
   selected account is applied to every monitored company.

For a company, enter a domain such as `example.com`. The first submission fetches the public HTTPS
homepage, reads its organization metadata and career links, registers a generic JSON-LD career
source, and runs that source immediately. The website remains the source of truth; no company
archive or ZIP import is required.

For a city, first use the explicit location search to select a German city or postal code. The
default Nominatim-compatible service resolves it to coordinates. The background city search first
uses recent German vacancy signals from the Bundesagentur für Arbeit, resolves matching employers
to public domains, and scans their discovered career or ATS feeds. If no employer domain can be
resolved, it falls back to the bounded OpenStreetMap Overpass website sweep. Newly discovered
companies and matching jobs appear in the feed as the worker processes them. City searches can be
run again later to pick up newly mapped employers; daily collection continues for all enabled
sources.

Set `COMPANY_LOCATION_API_URL`, `COMPANY_LOCATION_FALLBACK_API_URL`, `COMPANY_LOCATION_USER_AGENT`,
`COMPANY_LOCATION_LOOKUP_TIMEOUT_SECONDS`, `COMPANY_LOCATION_MAX_RESULTS`,
`COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS`, and
`COMPANY_LOCATION_RATE_LIMIT_STATE_PATH` to use a hosted Overpass-compatible service or tune a
self-hosted deployment. Set `LOCATION_API_URL`, `LOCATION_USER_AGENT`,
`LOCATION_MIN_REQUEST_INTERVAL_SECONDS`, and `LOCATION_RATE_LIMIT_STATE_PATH` for place lookup.
Set `COLLECTION_USER_AGENT` to the identifying User-Agent used for career-site scans. The default
company-location client allows enough time for the bounded Overpass query and its public queue;
keep that timeout at 45 seconds or higher unless using a faster private provider.
The public OpenStreetMap services require an identifying User-Agent, visible attribution, and
careful request rates. Map coverage is not a complete business registry, so a direct website can
always be added when a city search misses an employer.

The BA and Common Crawl clients are bounded and rate-limited. Configure their `BA_JOBS_*` and
`COMMON_CRAWL_*` settings in `.env`; a provider error is reported as a partial discovery and does
not replace prior snapshots or jobs. To run the orchestrator manually without an account target,
use `docker compose exec web python manage.py reverse_discover --city Berlin`. Add
`--no-collect` when only discovery metrics are wanted. Omitting `--city` performs the global Common
Crawl ATS probe. The scheduler runs the same global probe and refreshes every saved city target
daily; set `REVERSE_DISCOVERY_ENABLED=false` to disable that beat entry.

The location lookup endpoint is private to the selected workspace account and is intentionally not
an autocomplete API. An account with no monitored company or city has an empty feed until it
chooses where to look.

## Self-hosted deployment

See the [deployment guide](docs/deployment.md) for first startup, dynamic source discovery,
updates, backups, restore, and troubleshooting.

Install Docker Engine with the Compose plugin on the deployment machine. Then:

```bash
cp .env.example .env
# Edit .env and replace the secret values.
docker compose up --build -d
```

The web container exposes `/health/` for Docker health checks. It returns OK only when the
application can query the database.

Open `http://server:8000`. Set `APP_PORT` and `DJANGO_ALLOWED_HOSTS` in `.env` when the service
uses a different port or hostname.

Back up the PostgreSQL volume regularly. A logical backup can be created with:

```bash
docker compose exec -T db pg_dump -U jobs jobs > jobs-backup.sql
```
