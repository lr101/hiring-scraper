# Contributing to Radius

Bug reports, documentation fixes, tests, and focused improvements are welcome.
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before starting

- Search existing issues and pull requests before opening a new one.
- Use the bug or feature request form. Discuss substantial changes in an issue
  before implementing them.
- Report vulnerabilities using [SECURITY.md](SECURITY.md).
- Use sanitized examples. Do not submit CVs, personal contact details, credentials,
  `.env` files, database dumps, or private scraper captures. Preserve attribution
  and source licenses when contributing data or fixtures.

## Development and checks

Fork the repository and create a branch from `main`. Follow the
[README](README.md#local-development) to run the API and frontend. For the complete
test, build, workflow lint, and container smoke checks, use:

```bash
./scripts/ci/run.sh
```

See [the CI guide](docs/ci.md) for Docker requirements and troubleshooting. The
release validator and its tests can also run with Python 3.11+ without installing
application dependencies:

```bash
python3 scripts/ci/release_notes.py
python3 -m unittest discover -s tests -p test_release_notes.py -v
```

Keep changes focused, add regression coverage for behavior changes, and update
documentation when commands or behavior change. Use descriptive commit and PR
titles; Conventional Commits are welcome but not required.

## Changelog entries

Add a concise, user-facing bullet under `## [Unreleased]` in
[CHANGELOG.md](CHANGELOG.md) for features, bug fixes, breaking changes, migrations,
or security fixes. Use `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, or
`Security` headings as appropriate. Describe the effect and any required user
action, rather than listing implementation details.

Internal refactors, tests, routine dependency updates, and typo fixes may omit an
entry when they have no user-visible effect; explain the omission in the PR.
Do not bump the application version or create a dated release section in an
ordinary feature PR. Maintainers do that in a release preparation PR.

## Pull requests

Complete the PR template with the problem, resulting behavior, related issues,
and validation results. Disclose migration or deployment requirements. A
maintainer reviews the change and CI must pass before merging. If a check cannot
run locally, say which check and why; GitHub CI still needs to pass.

Release preparation is documented in [docs/ci.md](docs/ci.md#preparing-a-release).
Repository administrators can use the [maintainer checklist](docs/maintaining.md).

## Licensing

The software is licensed under the [MIT License](LICENSE). Contributions are
provided under the same license. Only submit work you have the right to contribute.
Third-party data retains its original terms, including OpenStreetMap attribution
requirements; the software license does not relicense source data.
