# Container CI and releases

Run the same checks locally and in GitHub Actions:

```bash
./scripts/ci/run.sh
```

Use a running Docker Engine with Buildx and Docker Compose 2.20+, Bash, and standard shell utilities. No host Python, Node.js, database, or `.env` is required. Initial builds download base images and locked dependencies; application checks use the committed fixtures rather than live hiring-site crawls.

Stack startup has a five-minute timeout. For slower Docker hosts, including software-emulated virtual machines, give migrations and the full fixture import more time:

```bash
HIRING_CI_WAIT_TIMEOUT=1200 ./scripts/ci/run.sh
```

The value must be a positive whole number of seconds. It changes the stack startup budget; the GitHub job retains its 30-minute limit.

## What the checks cover

The script builds a quality image with Python and Node, then runs actionlint for the workflows, ShellCheck for the CI and database setup scripts, all Python unittest suites, the frontend profile regression tests, and the TypeScript/Vite production build inside containers. Python unit tests exercise the SQLite application path. It also builds the production API/worker and web images, then starts an isolated PostgreSQL/PostGIS Compose stack to exercise migrations, repeatable fixture imports, API requests, and the production web server.

Each invocation uses a unique Compose project and disposable database volume. Cleanup removes that run's containers, network, and volumes on success or failure; the deployment's `hiring_db` volume is separate. Build caches and local images remain available for subsequent runs. Logs are written to `.ci-results/`, which is ignored by Git. The workflow uploads only `.log` and `.txt` files from that directory, including logs from failed checks, with seven-day retention. Dotenv files, database contents, and private source captures are not included in artifacts. Docker build contexts exclude local `.env` files, private `data/`, and CV source files; the committed sanitized fixtures remain available to tests and seeds.

To investigate a failure, read the failing command in the terminal or workflow log and inspect the corresponding `.ci-results/` log. Fix the underlying failure and rerun the complete command. The isolated database is intentionally removed after the run.

Health logs preserve probe timestamps, exit codes and output before teardown. The [local verification record](container-ci-verification.md) documents the successful full Docker run and the startup issues it uncovered.

## GitHub workflow

[Container CI](../.github/workflows/container-images.yml) runs on pull requests, pushes to `main`, pushes of `v*` tags, and manual dispatch. It has no path filters, so documentation-only pull requests also produce the required check. Require the stable **Container checks** status in a ruleset or branch protection rule for `main`; optionally require review and an up-to-date branch. Repository administrators configure those GitHub settings separately from the workflow.

Pull requests and manual dispatch run checks without registry login or publication. The workflow's default token permission is `contents: read`, and checkout does not persist credentials. Publication uses a separate job with `packages: write`, which only runs after checks pass on a push to `main` or a `v*` tag. There is no `pull_request_target` trigger. New commits cancel older checks for the same pull request; publication jobs are not canceled by concurrency settings. Both jobs have a 30-minute timeout.

Actions are pinned to verified release commit hashes, with their versions recorded beside the pins. Build base images are pinned to the manifest digests resolved by Docker so checks and publishing use the same bases. [Dependabot](../.github/dependabot.yml) checks actions, Docker images, Compose images, `frontend/package-lock.json`, and `uv.lock` weekly. Dependency updates go through the same container checks. GitHub documents the supported [Dependabot ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).

## Published images

After successful checks, the workflow builds and publishes these `linux/amd64` images to GHCR:

```text
ghcr.io/lr101/hiring-scraper/hiring-scraper-api
ghcr.io/lr101/hiring-scraper/hiring-scraper-web
```

The owner and repository are converted to lowercase. The API image also serves the workers. Publishing uses the repository's `GITHUB_TOKEN`; no personal access token is needed for the workflow. Ensure repository Actions and package policies permit publishing. For an existing package, grant the repository Actions access in the package settings. Private package consumers need their own read access; public image pulls need no login.

| Trigger | Published tags on each image |
| --- | --- |
| Push to `main` | Full commit SHA and `latest` |
| Push tag `v1.2.3` | Full commit SHA, `v1.2.3`, and normalized `1.2.3` |
| Push another `v*` tag | Full commit SHA and the sanitized Git tag; a normalized version is added when it is valid semver |
| Pull request or manual dispatch | No publication |

Release tags never move `latest`. Tag normalization follows the official [Docker metadata action](https://github.com/docker/metadata-action#image-name-and-tag-sanitization). Images include OCI source and revision labels, and API/web publish builds use separate GitHub Actions cache scopes. The two images publish independently after the shared gate; a registry failure can leave only one published, so check both publish jobs before deploying a release.

For deployments, prefer a full commit SHA or digest and set both image references in `.env`:

```dotenv
HIRING_API_IMAGE=ghcr.io/lr101/hiring-scraper/hiring-scraper-api:FULL_COMMIT_SHA
HIRING_WEB_IMAGE=ghcr.io/lr101/hiring-scraper/hiring-scraper-web:FULL_COMMIT_SHA
```

```bash
docker compose pull
docker compose up -d --no-build
```

The repository is [lr101/hiring-scraper](https://github.com/lr101/hiring-scraper). Follow [GitHub Actions](https://github.com/lr101/hiring-scraper/actions/workflows/container-images.yml) for check and publication results. Repository administrators configure branch protection or rulesets; the workflow supplies the **Container checks** status.
