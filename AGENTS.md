# Development instructions

## Local setup

This development environment already runs inside a container. Do not install or start Docker here.

Use mise for the Python runtime and `uv` for project dependencies:

```bash
mise trust
mise run setup
```

Run checks with:

```bash
mise run check
```

The committed Docker deployment files are for a Docker-capable self-hosted machine. Do not expect
`docker compose` commands to work inside this development container.

## Development process

- Write a failing test before production behavior.
- Use migrations for model changes.
- Run the focused test during development and the full check task before each feature commit.
- Keep external HTTP responses in test fixtures and never call employer websites from unit tests.
- Commit completed features separately using conventional commit messages.

