# Container verification — 2026-10-07

The complete container pipeline passed with exit status **0**:

```bash
HIRING_CI_WAIT_TIMEOUT=1200 ./scripts/ci/run.sh
```

This ran in genuine Docker containers in a temporary Debian VM. Software emulation was needed because this workspace cannot run a Docker daemon with the required Linux namespace privileges. The larger startup budget allowed the full fixture import to finish on that slower host; normal runs default to 300 seconds.

| Check | Observed result |
| --- | --- |
| Docker runtime | Engine 29.8.2; Compose 5.6.0 |
| Quality image | Python 3.13.16; Node 24.21.0; npm 11.19.0 |
| Workflow and shell checks | actionlint 1.7.7 and ShellCheck 0.9.0 passed |
| Python regression suite | 348 tests passed |
| Frontend | Profile regression checks and TypeScript/Vite production build passed |
| Production images | API/worker and nginx web images built successfully |
| Fresh PostgreSQL/PostGIS stack | Migrations, seed, API readiness, web health and nginx configuration passed |
| Repeat fixture import | Counts remained 2,251 companies, 210 feeds and 1,664 jobs |
| Application role | Database checks and repeat import ran as `hiring_app` |
| Web/API smoke | Assets and SPA routes, API proxy, profile create/edit/validation/delete, regional defaults, unique pagination, list/detail agreement and country override passed |
| Cleanup | Exit status 0; this run's containers, database volume and network were removed |

The successful Compose project was `hiring-ci-1791397113-0e2c7676`. Its raw logs are retained locally under ignored `.ci-results/`. The tested source archive SHA-256 was `2057ebb8c7fcc5d25ec6987804161748e0f29833acb6a0915118ea06e26a8fcc`; these verification notes and the roadmap status update were added afterward.

## Issues found and corrected

- Crawl manifests assumed Git was installed. Source archives and production images now record unknown Git revision/dirty state when unavailable, while retaining source-file hashes. Focused tests failed before the fix and passed afterward.
- The database healthcheck accepted the temporary Unix-socket bootstrap server before TCP was available. It now checks `127.0.0.1` over TCP. The [official PostgreSQL entrypoint](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh) starts a socket-only server for initialization; the failed migration logs showed connection refusal during that stage.
- The full import exceeded the original startup budget under software emulation. `HIRING_CI_WAIT_TIMEOUT` now supports slower hosts and rejects invalid values before invoking Docker.
- Docker killed the three-second API health probe even when the endpoint returned HTTP 200. A focused reproduction failed at three seconds and reported healthy at ten seconds. The outer probe budget is now ten seconds; the HTTP request timeout remains two seconds. CI artifacts also include probe timestamps, exit codes and output, without container environment/configuration dumps.

A cheaper-model code audit found bounded CPU work in the fixture import and 245 repeated occurrences among its nonempty descriptions. Memoizing normalized description text is a possible cost experiment; whole enrichment results also depend on each job's title, raw metadata and source hash. No parser or ranking change was needed for this container setup.

Hosted GitHub Actions, GHCR publication, repository protection settings, deployed image pulls, and the live crawler-worker restart lifecycle were not exercised. This checkout has no Git remote. The smoke starts the database, migration, seed, API and web services and uses committed fixtures. See [CI and release instructions](ci.md) for the local command, GitHub gate and publication configuration.
