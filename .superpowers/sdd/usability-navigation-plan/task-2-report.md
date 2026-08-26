# Task 2 report: three-page product shell

## Scope delivered

- Added named `feed`, `search`, and `profile` routes. The existing `home` route now renders Feed.
- Kept legacy profile, exclusion, job, company, source, run, account, and place-search routes unchanged.
- Added account-scoped company and city monitoring target forms and removal actions.
- Feed applies `filter_jobs_for_user`, excludes jobs ignored by the active account, and renders compact job cards.
- Profile links to existing profile and exclusion management, lists account-scoped workflow jobs, and supports stable `status` and `sort` query parameters. `sort=recent` is the default and `sort=company` orders by company name.
- Replaced the primary navigation with Feed, Search, and Profile. Operational links remain on Profile.
- Added responsive card and form styles. City selection queries the existing local `place_search_json` endpoint.

No employer website was contacted. Task 1's committed `0006_monitoring_targets` migration supplies the monitoring-target model; `makemigrations --check --dry-run` reported no further model changes.

## TDD evidence

### RED

Command:

```text
mise run test -- tests/test_navigation_pages.py
```

Output summary:

```text
collected 5 items
tests/test_navigation_pages.py FFFFF
5 failed, 1 warning
```

The failures were expected `NoReverseMatch` errors for the absent `jobs:feed`, `jobs:search`, `jobs:profile`, and target-action routes.

### GREEN

Command:

```text
mise run test -- tests/test_navigation_pages.py
```

Output summary:

```text
collected 5 items
tests/test_navigation_pages.py .....
5 passed, 5 warnings
```

The warnings come from the pre-existing absence of the local `staticfiles/` directory in the Django test environment.

## Verification

Formatting and lint fixes were applied after `mise run check` reported the exact files, then the full check was rerun. The compatibility smoke assertion for the existing "New jobs" text was retained in Feed copy.

Commands:

```text
mise exec -- uv run python manage.py makemigrations --check --dry-run
mise run test -- tests/test_smoke.py tests/test_navigation_pages.py
mise run check
```

Output summaries:

```text
No changes detected

collected 9 items
9 passed, 7 warnings

45 files already formatted
All checks passed!
Success: no issues found in 38 source files
collected 128 items
128 passed, 37 warnings
```

I also ran `git diff --check` and reviewed the complete task diff before committing.
