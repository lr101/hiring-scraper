# Usability navigation plan

## Global constraints

- The service stays private, self-hosted, English-only, server-rendered, and desktop-first with a responsive fallback.
- The active account comes from the existing explicit workspace cookie. Profiles, exclusions, monitoring targets, saved jobs, applied jobs, and notes stay account-scoped.
- The top navigation contains exactly three product pages: Feed, Job profiles, and Where to look. Existing operational pages remain routable as secondary tools but do not appear in the product navigation.
- Feed is the account's stream of open, new matches from its monitored sources. Each item is a short overview card linking to the existing job detail workflow. If an account has no monitored company or city, the feed shows setup guidance and no jobs.
- Job profiles describe only the roles, skills, metadata, and scoring preferences to match. Multiple profiles can be enabled in one account, and each enabled profile is applied to every company monitored by that account.
- Where to look is the single account-level place for adding and removing companies and city searches. A direct domain discovers the company's website and career source. A city search resolves a place, finds mapped businesses with public websites, adds their companies, and starts their first scans.
- A city search stores a configurable radius in kilometres and can be run again to find newly mapped employers. Company targets match all jobs from that company. City targets also retain the existing nearby-job filter for shared sources: coordinates are preferred, with exact normalized city-name fallback when coordinates are unavailable.
- Profile is the account home for the three setup areas and exclusions. It also lists the account's jobs with a saved or workflow status, with status filtering and sorting.
- “Starred” and “saved” use the existing `UserJobState.Status.SAVED` state. Existing workflow states remain available in the profile list.
- Preserve current CRUD routes and operational pages for compatibility, except for the retired place-dictionary page, JSON endpoint, and old Search page/target URLs. New pages may link to remaining operational tools as secondary actions.
- Use migrations for schema changes. Do not add a JavaScript framework. Discover companies from a submitted domain and use configurable Nominatim and Overpass-compatible services only for explicit user actions. Cache successful places locally, show provider attribution, bound company results, and keep the location endpoint private rather than exposing autocomplete.
- Add tests before production behavior and record the red and green runs for each task. Use fixture data only; tests must not call employer websites.

## Task 1: Account monitoring targets and filtering

Add a `MonitoringTarget` model with one account, a company-or-city kind, the selected company or German place, and a city radius. Add database constraints preventing malformed targets and duplicate targets for one account. Add a small `jobs.monitoring` module that preserves input order and filters a job iterable by the active account's targets. Cover no-target behavior, company matching, city-radius matching, exact city fallback for jobs without coordinates, and account isolation with focused tests in `tests/test_monitoring.py`.

Files: `jobs/models.py`, `jobs/migrations/0006_monitoring_targets.py`, `jobs/monitoring.py`, `tests/test_monitoring.py`.

## Task 2: Three-page product shell

Add the profile dashboard, job-profile page, Where to look page, and feed page using the existing models and Task 1's monitoring helper. Extend forms with company and city target forms, including safe local place lookup. Add routes named `profile`, `setup`, and `feed` while keeping `home`, `profile_list`, and existing operational routes working. Make `/` render the feed. The profile dashboard must show profile, source setup, and exclusion configuration entry points plus saved/workflow jobs with a status filter and sort choice. The setup page must be the only place that lists and changes company/city targets, accept direct domains, find companies near a selected city, and offer a repeat city search. A direct company or newly discovered company must run its source immediately. The feed must apply monitoring targets, ignore account-ignored jobs, and render compact cards with company, location/work mode, score, and a clear next action. Update the base template and CSS so only Feed, Job profiles, and Where to look are in the primary nav, the active account remains visible, forms and cards work on narrow screens, and secondary operational links are discoverable from Profile.

Add integration tests in `tests/test_navigation_pages.py` and `tests/test_account_source_flow.py` for the three-page routes, account scoping, target creation/removal, target filtering, direct-domain and city discovery, immediate scans, repeat city searches, saved-job filtering/sorting, and primary-nav labels.

Files: `jobs/forms.py`, `jobs/views.py`, `jobs/urls.py`, `templates/base.html`, `templates/jobs/home.html`, `templates/jobs/profile_page.html`, `templates/jobs/setup.html`, `static/app.css`, `tests/test_navigation_pages.py`, `tests/test_account_source_flow.py`.

## Acceptance checklist

- `mise run check` passes on the feature branch.
- The branch contains the new model migration and focused tests.
- A selected account can reach Feed, Job profiles, and Where to look without exposing another account's data.
- The primary nav has no legacy page links.
- The existing profile, exclusion, job detail, source, company, run, and dynamic-location tests remain green.
