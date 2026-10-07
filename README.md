# Radius

Radius discovers employers and job openings in Germany. Local development uses SQLite; Compose runs PostgreSQL/PostGIS and imports the Karlsruhe sample on startup.

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

## Compose

Copy `.env.example` to `.env`, then replace both placeholder passwords with different random hex values before starting:

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

Open <http://localhost:8080>.

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
