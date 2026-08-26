# Usability navigation plan

## Global constraints

- The service stays private, self-hosted, English-only, server-rendered, and desktop-first with a responsive fallback.
- The active account comes from the existing explicit workspace cookie. Profiles, exclusions, monitoring targets, saved jobs, applied jobs, and notes stay account-scoped.
- The top navigation contains exactly three product pages: Feed, Search, and Profile. Existing operational pages remain routable as secondary tools but do not appear in the product navigation.
- Feed is the account's stream of open, new matches. Each item is a short overview card linking to the existing job detail workflow.
- Search lets the active account add and remove companies and German places to monitor. Monitoring targets are account filters. If an account has no targets, all matched jobs remain eligible for its feed.
- A city monitoring target stores a configurable radius in kilometres. Company targets match all jobs from that company. City targets match jobs by coordinates within the radius and fall back to an exact normalized city name when a job has no coordinates.
- Profile is the account home for job profiles and exclusions. It also lists the account's jobs with a saved or workflow status, with status filtering and sorting.
- “Starred” and “saved” use the existing `UserJobState.Status.SAVED` state. Existing workflow states remain available in the profile list.
- Preserve current CRUD routes and operational pages for compatibility, except for the retired place-dictionary page and JSON endpoint. New pages may link to remaining operational tools as secondary actions.
- Use migrations for schema changes. Do not add a JavaScript framework. Discover companies from a submitted domain and use a configurable location API only for explicit user searches. Cache successful places locally, show provider attribution, and keep the location endpoint private rather than exposing autocomplete.
- Add tests before production behavior and record the red and green runs for each task. Use fixture data only; tests must not call employer websites.

## Task 1: Account monitoring targets and filtering

Add a `MonitoringTarget` model with one account, a company-or-city kind, the selected company or German place, and a city radius. Add database constraints preventing malformed targets and duplicate targets for one account. Add a small `jobs.monitoring` module that preserves input order and filters a job iterable by the active account's targets. Cover no-target behavior, company matching, city-radius matching, exact city fallback for jobs without coordinates, and account isolation with focused tests in `tests/test_monitoring.py`.

Files: `jobs/models.py`, `jobs/migrations/0006_monitoring_targets.py`, `jobs/monitoring.py`, `tests/test_monitoring.py`.

## Task 2: Three-page product shell

Add the profile dashboard, search page, and feed page using the existing models and Task 1's monitoring helper. Extend forms with company and city target forms, including safe local place lookup. Add routes named `profile`, `search`, and `feed` while keeping `home`, `profile_list`, and existing operational routes working. Make `/` render the feed. The profile dashboard must show profile and exclusion configuration entry points plus saved/workflow jobs with a status filter and sort choice. The search page must list current targets and accept/remove company and city targets. The feed must apply monitoring targets, ignore account-ignored jobs, and render compact cards with company, location/work mode, score, and a clear next action. Update the base template and CSS so only Feed, Search, and Profile are in the primary nav, the active account remains visible, forms and cards work on narrow screens, and secondary operational links are discoverable from Profile.

Add integration tests in `tests/test_navigation_pages.py` for the three-page routes, account scoping, target creation/removal, target filtering, saved-job filtering/sorting, and primary-nav labels.

Files: `jobs/forms.py`, `jobs/views.py`, `jobs/urls.py`, `templates/base.html`, `templates/jobs/home.html`, `templates/jobs/profile_page.html`, `templates/jobs/search.html`, `static/app.css`, `tests/test_navigation_pages.py`.

## Acceptance checklist

- `mise run check` passes on the feature branch.
- The branch contains the new model migration and focused tests.
- A selected account can reach Feed, Search, and Profile without exposing another account's data.
- The primary nav has no legacy page links.
- The existing profile, exclusion, job detail, source, company, run, and dynamic-location tests remain green.
