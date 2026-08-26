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

Add a company from the Search page by entering its domain or pasting its website URL. The first
submission fetches the public HTTPS homepage, reads its organization metadata and career links,
and registers a generic JSON-LD career source. The website remains the source of truth; no company
archive or ZIP import is required.

City and postal-code searches use the configured Nominatim-compatible location API only after the
user presses Search. Results are cached in PostgreSQL, so profiles can use the selected place on
future requests. Set `LOCATION_API_URL`, `LOCATION_USER_AGENT`, and
`LOCATION_MIN_REQUEST_INTERVAL_SECONDS` when using a hosted or self-managed provider. The default
public provider requires visible OpenStreetMap attribution and an identifying User-Agent.

The location lookup endpoint is private to the selected workspace account and is intentionally not
an autocomplete API.

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
