# Deployment

This guide deploys Hiring Scraper on a Linux machine with Docker Engine and the Compose plugin.
Run the commands below from a checkout of this repository on the deployment machine. Docker is
not required inside the development container.

## Requirements

- A Docker-capable Linux host with enough disk space for PostgreSQL and Redis data.
- Docker Engine and Docker Compose v2.
- A hostname or IP address reachable from your browser.

## First startup

Create the environment file and replace both placeholder secrets. Generate the Django key with a
password manager or a command such as `openssl rand -base64 48`.

```bash
cp .env.example .env
${EDITOR:-vi} .env
```

Set `DJANGO_ALLOWED_HOSTS` to the hostnames used in the browser. Use comma-separated values with no
URL scheme, for example `jobs.example.net,localhost`. Keep `DJANGO_DEBUG=false` on a shared or
internet-facing host. `APP_PORT` controls the host port, while the application always listens on
container port 8000.

Build and start the services:

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 web
```

The web container runs migrations and collects static files before starting Gunicorn. The web
health check calls `/health/`, which returns HTTP 200 only after the application can query
PostgreSQL. Check it directly with:

```bash
curl -fsS http://127.0.0.1:${APP_PORT:-8000}/health/
```

Open `http://HOSTNAME:${APP_PORT:-8000}/` in a browser. The application has no login system. It
uses the selected account cookie to keep profiles, exclusions, matches, notes, and workflow state
separate. Create the first account from the Accounts page, select it, and then create a profile.

## Load places and seed sources

Download GeoNames archives on the deployment machine. The application does not download them for
you. Import at least `cities500.zip`; add the Germany postal-code archive if postal-code choices
are useful in profiles.

```bash
docker compose cp /data/cities500.zip web:/tmp/cities500.zip
docker compose cp /data/DE.zip web:/tmp/DE.zip
docker compose exec web python manage.py import_german_places \
  --cities /tmp/cities500.zip \
  --postal-codes /tmp/DE.zip \
  --snapshot 2026-08-26
```

Use the date represented by the downloaded source snapshot. Repeating the import is safe. The
import command updates existing places and profile location coordinates.

Create the five initial company sources. This command is idempotent and does not change existing
source settings:

```bash
docker compose exec web python manage.py seed_initial_sources
```

Review the Sources page before the first run. A source is disabled after bot protection is
detected. Do not bypass that state with proxy rotation or CAPTCHA solving. Unblock it manually
only after checking the site and its request settings.

Run all enabled sources once from the Sources page, or use the command line:

```bash
docker compose exec web python manage.py collect_jobs
```

The scheduler runs the same collection task daily. Collection results, blocked sources, and
failures are visible on the Runs page.

## Updates

Fetch the new revision and rebuild the application. Compose preserves the PostgreSQL and Redis
volumes across this operation.

```bash
git pull --ff-only
docker compose up --build -d
docker compose ps
```

The web startup script applies pending migrations before serving traffic. Do not remove the named
volumes during an update.

## Backups and restore

Create a logical PostgreSQL backup on the host. Keep backups outside the repository and encrypt
them when they contain private notes or account data.

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U jobs jobs > backups/jobs-$(date +%F).sql
```

To restore, stop the application and worker services so they cannot write during the restore. The
restore below replaces the database contents in the existing PostgreSQL volume.

```bash
docker compose stop web worker scheduler
docker compose exec -T db psql -U jobs -d jobs -c \
  'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose exec -T db psql -U jobs -d jobs < backups/jobs-YYYY-MM-DD.sql
docker compose up -d web worker scheduler
```

Replace `YYYY-MM-DD` with the backup date. Check `/health/`, the Accounts page, and the Runs page
after restoration. The Redis volume contains task state and is not a substitute for a database
backup.

## Troubleshooting

Inspect service status and recent logs first:

```bash
docker compose ps
docker compose logs --tail=200 web worker scheduler db redis
```

Common causes:

- `web` is unhealthy: check the database logs and confirm `POSTGRES_PASSWORD` is set in `.env`.
- The site is unreachable: check the host firewall and the `APP_PORT` mapping.
- A hostname is rejected: add the exact hostname, without `https://`, to `DJANGO_ALLOWED_HOSTS`.
- A source is blocked: inspect the latest run, then use the Sources page to unblock only after
  reviewing the cause.
- Profiles have no city choices: import the GeoNames places into the running web container.

Never publish PostgreSQL or Redis ports directly to the internet. Put HTTPS and authentication in
front of the application if it will be reachable beyond a trusted private network.
