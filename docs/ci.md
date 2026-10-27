# Container CI and releases

Run the same checks locally and in GitHub Actions:

```bash
./scripts/ci/run.sh
```

Use a running Docker Engine with Buildx and Docker Compose 2.20+, Bash, and standard shell utilities. No host Python, Node.js, database, or `.env` is required. Initial builds download base images and locked dependencies; application checks use the committed fixtures rather than live hiring-site crawls.

Stack startup has a five-minute health-check timeout. For slower Docker hosts, including software-emulated virtual machines, give PostgreSQL startup and migrations more time:

```bash
HIRING_CI_WAIT_TIMEOUT=1200 ./scripts/ci/run.sh
```

The value must be a positive whole number of seconds. It changes the stack startup budget; the GitHub job retains its 30-minute limit.

## What the checks cover

The script builds a quality image with Python and Node, then runs actionlint for the workflows, ShellCheck for the CI scripts, changelog validation, Python unittest suites, extension formatting, and frontend/extension Node regression checks inside containers. Python unit tests exercise the SQLite application path; browser suites are skipped unless explicitly enabled. The combined app image runs the TypeScript/Vite production build, then an isolated PostgreSQL/PostGIS Compose stack exercises migrations, repeatable fixture imports, API requests, and the built frontend. The CI override disables background crawlers during the smoke run.

The PostgreSQL smoke test forces stale collation metadata, replays the repair migration, and verifies the repaired database and usable company index. It checks the applied revisions against Alembic's migration graph before and after repair. Adding a migration does not require updating a hard-coded expected head. A source-order test for historical migration SQL was removed in favor of exercising the real migration. A small compatibility guard retains the current Debian/glibc PostgreSQL base because deployed databases reuse a persistent cluster whose indexes depend on that collation provider.

Each invocation uses a unique Compose project and disposable database volume. Cleanup removes that run's containers, network, and volumes on success or failure; the deployment's `hiring_db` volume is separate. Build caches and local images remain available for subsequent runs. Logs are written to `.ci-results/`, which is ignored by Git. The workflow uploads only `.log` and `.txt` files from that directory, including logs from failed checks, with seven-day retention. Dotenv files, database contents, and private source captures are not included in artifacts. Docker build contexts exclude local `.env` files, private `data/`, and CV source files; the committed sanitized fixtures remain available to tests and seeds.

To investigate a failure, read the failing command in the terminal or workflow log and inspect the corresponding `.ci-results/` log. Fix the underlying failure and rerun the complete command. The isolated database is intentionally removed after the run.

Health logs preserve probe timestamps, exit codes and output before teardown. The [local verification record](container-ci-verification.md) documents the successful full Docker run and the startup issues it uncovered.

## GitHub workflow

[Container CI](../.github/workflows/container-images.yml) runs on pull requests, pushes to `main`, pushes of `v*` tags, and manual dispatch. It has no path filters, so documentation-only pull requests also produce the required check. Require the stable **Container checks** status in a ruleset or branch protection rule for `main`; optionally require review and an up-to-date branch. Repository administrators configure those GitHub settings separately from the workflow.

Pull requests and manual dispatch run checks without registry login or publication. The workflow's default token permission is `contents: read`, and checkout does not persist credentials. Publication uses a separate job with `packages: write`, which only runs after checks pass on a push to `main` or a valid stable release tag. Tag checks require the tagged commit to be reachable from `origin/main` and reject malformed tags, versions that differ from `pyproject.toml`, and missing or empty dated changelog entries before publishing images. There is no `pull_request_target` trigger. New commits cancel older checks for the same pull request; publication jobs are not canceled by concurrency settings. Check and image jobs have a 30-minute timeout. A separate five-minute job requests `contents: write` to create the GitHub Release only after both images publish successfully.

### Keeping the pipeline fast

The successful PR run [40300241093](https://github.com/lr101/hiring-scraper/actions/runs/40300241093) took about ten minutes: 515 seconds for 133 extension tests, compared with 92 seconds for 857 backend tests. The pipeline therefore retains the backend regressions and all browser behavior scenarios, and reduces repeated work:

- Two browser jobs each run half the discovered tests, in deterministic test-ID order. New browser tests are included automatically. The stable **Radius extension** check requires both shards to succeed; one failure does not cancel the other shard's diagnostics.
- Node checks and extension formatting run once in the quality image. The packaging regression runs once in the backend suite. The frontend is built in the app image and in each browser job that consumes it, without an additional unused quality-image build.
- Buildx Bake builds all three CI images with independent GitHub Actions layer caches. App and database cache scopes are shared with publication. The pinned semantic-model download precedes application source copies, so backend edits can reuse that layer. Cache misses still build and test the full images; no registry write credentials are needed on PRs.
- Ordinary checks use a shallow checkout; tag validation keeps full history for its ancestry check.

The workflow builds fixed local image tags through `compose.ci.yaml` and `docker-bake.ci.hcl`, then runs `HIRING_CI_PREBUILT=1 ./scripts/ci/run.sh`. This mode requires all three images to exist and retags them into the invocation's private Compose project. Normal local invocations still build images automatically. Cold runs populate the caches; warm-run timing depends on which dependency layers changed. Cache exports have a two-minute budget and export failures do not fail otherwise valid image builds.

To reproduce the browser jobs after installing the locked Python/frontend dependencies, full Playwright Chromium, and the PDF/TeX runtime, build the frontend and run each shard:

```bash
npm --prefix frontend run build
RADIUS_EXTENSION_BROWSER_TESTS=1 uv run python scripts/ci/test_shard.py --top-level-directory tests --shard 1 --shards 2
RADIUS_EXTENSION_BROWSER_TESTS=1 uv run python scripts/ci/test_shard.py --top-level-directory tests --shard 2 --shards 2
```

Unsharded discovery remains available with `RADIUS_EXTENSION_BROWSER_TESTS=1 uv run python -m unittest discover -s tests/application_companion -t tests -v`. Browser jobs use fresh contexts and disposable databases per test. They retain the shipped-package, real API/PDF, stale-document, navigation-race, consent, and no-automatic-submit regressions. Change-based test exclusion and broad removal of behavioral tests were avoided because they can hide cross-component regressions.

Actions are pinned to verified release commit hashes, with their versions recorded beside the pins. Build base images are pinned to the manifest digests resolved by Docker so checks and publishing use the same bases. [Dependabot](../.github/dependabot.yml) checks actions, Docker images, Compose images, `frontend/package-lock.json`, and `uv.lock` weekly. Dependency updates go through the same container checks. GitHub documents the supported [Dependabot ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).

## Published images

After successful checks, the workflow builds and publishes two `linux/amd64` image variants to the existing GHCR package:

```text
ghcr.io/lr101/hiring-scraper/hiring-scraper
ghcr.io/lr101/hiring-scraper/hiring-scraper:db-latest
```

The `latest` image contains the API, both background workers, and the built frontend. The `db-latest` image contains PostgreSQL 17, PostGIS 3.5, and pgvector. Database tags add a `db-` prefix to the matching commit, `latest`, and release-version tags so both images can share the existing public package without tag collisions. Publishing uses the repository's `GITHUB_TOKEN`; no personal access token is needed for the workflow. Both images include OCI source and revision labels. Public image pulls need no login.

| Trigger | Published tags |
| --- | --- |
| Push to `main` | App gets full commit SHA and `latest`; database gets `db-` prefixed equivalents |
| Push tag `v1.2.3` | App gets full commit SHA, `v1.2.3`, and `1.2.3`; database gets `db-` prefixed equivalents |
| Push an invalid or prerelease `v*` tag | Validation fails; no images or GitHub Release are published |
| Pull request or manual dispatch | No publication |

Release tags never move `latest` or `db-latest`. Tag normalization follows the official [Docker metadata action](https://github.com/docker/metadata-action#image-name-and-tag-sanitization). The GitHub Release job waits for both image publications to succeed.

## Preparing a release

The release source of truth is `pyproject.toml` plus [CHANGELOG.md](../CHANGELOG.md).
The private frontend package version is independent and does not need to match.
Only stable `vMAJOR.MINOR.PATCH` tags are supported. Use Semantic Versioning:
patch for compatible fixes, minor for compatible features, and major for breaking
changes. During `0.x` development, describe breaking changes explicitly and use a
new minor version for them.

1. Open a release preparation PR from `main`. Choose the next version; `0.2.0` is
   the existing application version and can be the first release if appropriate.
2. Set `project.version` in `pyproject.toml`, then run `mise exec -- uv lock` to
   update the lockfile's project version. Do not edit dependency versions by hand.
3. Move the relevant `Unreleased` bullets into a section such as
   `## [0.2.0] - YYYY-MM-DD`, using the actual release date. Keep an empty
   `## [Unreleased]` section above it. Include migration steps and breaking changes.
4. Validate the prepared entry and run the complete checks:

   ```bash
   python3 scripts/ci/release_notes.py --tag v0.2.0
   ./scripts/ci/run.sh
   ```

5. Merge the reviewed PR, then create and push an annotated tag on that commit.
   Replace `v0.2.0` with the prepared version throughout:

   ```bash
   git switch main
   git pull --ff-only origin main
   git tag -a v0.2.0 -m "Radius v0.2.0"
   git push origin v0.2.0
   ```

The tag push runs validation and the complete container checks, publishes both
GHCR images, then creates a published GitHub Release titled `Radius v0.2.0` using
only that version's changelog body. GitHub supplies source archives; this workflow
does not publish to PyPI or npm. Release creation uses the runner's GitHub CLI with
[`--verify-tag` and `--notes-file`](https://cli.github.com/manual/gh_release_create),
so it does not create an unprepared tag or generate notes from arbitrary commits.
GitHub determines the latest stable Release automatically; this is independent
of the GHCR `latest` tag, which continues to track `main`.

## Recovering a failed release

For a transient check, registry, or GitHub API failure, rerun the failed jobs in
the original tag-triggered Actions run. Manual workflow dispatch performs checks
only and cannot publish a release. Release creation leaves an existing release
unchanged, so rerunning a successful publication does not overwrite curated notes.
If the image published but release creation failed, rerun the release job.
An existing draft causes the job to fail rather than silently reporting success;
resolve the draft in GitHub before retrying.

If validation fails because the tagged source is wrong, fix it through a new PR
and use a new version and tag. Do not move or reuse a published tag. Fix incorrect
release notes deliberately in GitHub rather than expecting a rerun to edit them.
See [the maintainer checklist](maintaining.md) for rulesets, package permissions,
and private vulnerability reporting settings.

For deployments, prefer tags from the same full commit SHA, or pin each image to its own digest:

```dotenv
HIRING_APP_IMAGE=ghcr.io/lr101/hiring-scraper/hiring-scraper:FULL_COMMIT_SHA
HIRING_DB_IMAGE=ghcr.io/lr101/hiring-scraper/hiring-scraper:db-FULL_COMMIT_SHA
```

```bash
docker compose pull app db
docker compose up -d --no-build
```

The repository is [lr101/hiring-scraper](https://github.com/lr101/hiring-scraper). Follow [GitHub Actions](https://github.com/lr101/hiring-scraper/actions/workflows/container-images.yml) for check and publication results. Repository administrators configure branch protection or rulesets; the workflow supplies the **Container checks** status.
