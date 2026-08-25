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

## Self-hosted deployment

Install Docker Engine with the Compose plugin on the deployment machine. Then:

```bash
cp .env.example .env
# Edit .env and replace both secrets.
docker compose up --build -d
```

Open `http://server:8000`. Set `APP_PORT` and `DJANGO_ALLOWED_HOSTS` in `.env` when the service
uses a different port or hostname.

Back up the PostgreSQL volume regularly. A logical backup can be created with:

```bash
docker compose exec -T db pg_dump -U jobs jobs > jobs-backup.sql
```

