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

Save a job profile under **Your profile**, then choose an application status on a
job in the results or job details. **My applications** shows tracked postings for
each profile, with counts and filters for New, Open, Not interested, Waiting for
reply, Interview, Rejected, and Accepted. Statuses persist independently of the
source posting; closed and expired postings remain in your application overview.

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
