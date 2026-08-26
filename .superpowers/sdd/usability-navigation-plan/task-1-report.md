# Task 1 report

## Scope

Added account-scoped `MonitoringTarget` records for company and city targets. A target has one
workspace account, a kind, the selected company or German place, and a city radius. Database
constraints reject invalid target shapes, zero city radii, and duplicate company or city targets
for the same account. `filter_jobs_for_user` returns matching jobs in the iterable's original
order.

The implementation is limited to Task 1. It does not add Task 2 UI or contact employer sites.

## Test-first evidence

The tests name the behavior that would break if filtering returned all jobs, reordered jobs,
matched the wrong company or account, accepted a city outside its radius, used a non-exact city
fallback, or allowed invalid target rows.

### RED

Command:

```text
mise run test -- tests/test_monitoring.py
```

Output:

```text
[test] $ uv run pytest tests/test_monitoring.py
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-8.4.2, pluggy-1.6.0
django: version: 5.2.17, settings: config.settings (from ini)
rootdir: /workspace/hiring-scraper/.worktrees/usability-navigation
configfile: pyproject.toml
plugins: django-4.14.0, respx-0.22.0, anyio-4.14.2
collected 0 items / 1 error

==================================== ERRORS ====================================
__________________ ERROR collecting tests/test_monitoring.py ___________________
ImportError while importing test module '/workspace/hiring-scraper/.worktrees/usability-navigation/tests/test_monitoring.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/opt/mise/installs/python/3.13.15/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_monitoring.py:4: in <module>
    from jobs.models import CareerSource, Company, GermanPlace, Job, MonitoringTarget, WorkspaceUser
E   ImportError: cannot import name 'MonitoringTarget' from 'jobs.models' (/workspace/hiring-scraper/.worktrees/usability-navigation/jobs/models.py)
=========================== short test summary info ============================
ERROR tests/test_monitoring.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.18s ===============================
[test] ERROR task failed
```

The failure was expected. It proves the new tests required the missing monitoring model before
production code was added.

### GREEN

Command:

```text
mise run test -- tests/test_monitoring.py
```

Output:

```text
[test] $ uv run pytest tests/test_monitoring.py
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-8.4.2, pluggy-1.6.0
django: version: 5.2.17, settings: config.settings (from ini)
rootdir: /workspace/hiring-scraper/.worktrees/usability-navigation
configfile: pyproject.toml
plugins: django-4.14.0, respx-0.22.0, anyio-4.14.2
collected 6 items

tests/test_monitoring.py ......                                          [100%]

============================== 6 passed in 0.49s ===============================
```

The final focused run also passed: 6 passed in 0.46s.

## Verification

The first full check found only formatting drift in the generated migration and new helper:

```text
Would reformat: jobs/migrations/0006_monitoring_targets.py
Would reformat: jobs/monitoring.py
2 files would be reformatted, 42 files already formatted
```

`ruff format` corrected those two files. The final commands and results were:

```text
mise run check
44 files already formatted
All checks passed!
Success: no issues found in 37 source files
123 passed, 32 warnings in 2.44s

mise exec -- uv run python manage.py makemigrations --check --dry-run
No changes detected
```

The warnings are pre-existing development warnings that `staticfiles/` is absent. They do not
affect the test results.

## Files changed

- `jobs/models.py`
- `jobs/migrations/0006_monitoring_targets.py`
- `jobs/monitoring.py`
- `tests/test_monitoring.py`
