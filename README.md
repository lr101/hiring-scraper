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

## German place dictionary

Profiles use locally imported GeoNames coordinates. Download GeoNames `cities500.zip` and, if postal
code lookup is wanted, the Germany postal-code archive on the deployment machine. Then import the
files with the date of the downloaded source snapshot:

```bash
python manage.py import_german_places \
  --cities /data/cities500.zip \
  --postal-codes /data/DE.zip \
  --snapshot 2026-08-26
```

The command does not download data. It is safe to run again with the same files, and records the
snapshot date on each imported place. Contains information from GeoNames.org, licensed under CC BY
4.0; filtered and normalized by this application.

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
