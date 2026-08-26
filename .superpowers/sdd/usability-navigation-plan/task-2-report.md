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

## Fix round 1

### Scope delivered

- Feed now excludes only jobs ignored by the active account. Another account's ignored state no longer removes a shared job.
- Job detail and workflow state updates now allow either a current match or an existing state for the active account. A saved job remains available after its match is removed.
- Invalid company and city target submissions now render the bound Search page. Duplicate company targets, invalid city selections, and invalid radii show errors and retain submitted values.
- Profile's operational links now include the legacy Jobs tool.
- Navigation integration tests now cover mixed workflow statuses, status filtering and company sorting, city-radius filtering, normalized no-coordinate city filtering, no-target fallback, and the account-isolation regressions.

### TDD evidence

Initial RED command:

```text
mise run test -- tests/test_navigation_pages.py
```

Output summary:

```text
collected 13 items
5 failed, 8 passed, 13 warnings
```

The expected failures showed redirects instead of bound invalid forms, an active account losing a job because another account ignored it, a saved job detail returning 404, and the missing Jobs operational link.

An additional city-selection error test was added during final review.

```text
mise run test -- tests/test_navigation_pages.py
collected 14 items
1 failed, 13 passed, 14 warnings
```

It failed because the hidden city field's validation error was not rendered.

GREEN command:

```text
mise run test -- tests/test_navigation_pages.py
```

Output summary:

```text
collected 14 items
14 passed, 14 warnings
```

### Final verification

Commands:

```text
mise exec -- uv run python manage.py makemigrations --check --dry-run
mise run check
```

Output summaries:

```text
No changes detected

45 files already formatted
All checks passed!
Success: no issues found in 38 source files
collected 137 items
137 passed, 46 warnings
```

The warnings are the existing Django notice that the local `staticfiles/` directory is absent during tests. No employer website was contacted.

## Final review fix wave

### Scope delivered

- Added `restore_current_migration_leaf`, a yielded fixture used by both migration tests. Its teardown always migrates the shared test connection back to `jobs.0006_monitoring_targets`, including when a migration assertion fails. The migration assertions themselves still exercise the historical `0005` state.
- Added a real `filter_jobs_for_user` test with two matching jobs supplied in deliberate reverse order. It confirms the helper returns matching jobs in input order.

### TDD evidence

The first invocation did not reproduce the order failure because pytest-django moved the non-transactional monitoring test ahead of the transactional migration test:

```text
mise run test -- tests/test_profiles.py::test_profile_location_migration_reverse_merges_same_city_places_per_profile tests/test_monitoring.py::test_filter_jobs_for_user_preserves_input_order_for_multiple_matching_jobs
```

```text
collected 2 items
tests/test_monitoring.py .
tests/test_profiles.py .
2 passed
```

The new order test was then marked transactional so the requested migration-before-monitoring order could run. Before the cleanup fixture, the same command produced the expected RED failure:

```text
collected 2 items
tests/test_profiles.py .
tests/test_monitoring.py F
1 failed, 1 passed
django.db.utils.OperationalError: no such table: jobs_monitoringtarget
```

After adding the cleanup fixture, the same reordered command was GREEN:

```text
collected 2 items
tests/test_profiles.py .
tests/test_monitoring.py .
2 passed
```

### Final verification

Commands:

```text
mise run test -- tests/test_profiles.py tests/test_monitoring.py
mise exec -- uv run python manage.py makemigrations --check --dry-run
mise run check
```

Output summaries:

```text
collected 32 items
32 passed, 15 warnings

No changes detected

45 files already formatted
All checks passed!
Success: no issues found in 38 source files
collected 138 items
138 passed, 46 warnings
```

The warnings are the existing Django notice that the local `staticfiles/` directory is absent during tests. No employer website was contacted.
