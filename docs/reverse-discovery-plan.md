# Reverse job discovery implementation plan

## Goal

Change company discovery from broad geographic enumeration to active hiring signals. The
application should be able to find employers with recent German vacancies, identify a known ATS
feed when possible, and send normalized jobs through the existing collection and matching pipeline.

## Global constraints

- Germany is the only target country. Discovery may request a German city and radius, but every
  imported job must have positive Germany evidence or remain out of the complete collection.
- The Bundesagentur für Arbeit Jobsuche endpoint is an undocumented or community-documented
  integration. Keep its base URL, API key, timeout, result limit, and request interval configurable;
  a provider failure must not delete existing jobs or claim a complete empty result.
- Public ATS feeds are read-only inputs. Never submit applications, send credentials, or follow
  arbitrary payload URLs. Validate HTTPS hosts and respect the existing bot-protection behavior.
- Common Crawl is a bounded discovery index, not a source of job truth. Extract only recognized
  ATS tenant identifiers and validate each live feed before creating a company source.
- Existing direct-domain and OSM setup paths must keep working. New global discovery must not attach
  companies to every workspace account implicitly.
- Use the existing `RawJob`, `CollectionResult`, `CareerSource`, and `collect_source` lifecycle so
  matching, closure, ignored reposts, and run accounting stay unchanged.
- Do not make employee count a prerequisite. Store or calculate hiring activity from active jobs,
  recent jobs, locations, ATS presence, and career-page presence when the data is available.
- All external responses in tests must live in fixtures or deterministic mock transports. Unit and
  integration tests must not call live employer, BA, Common Crawl, or map services.
- Use migrations for schema changes, test-first development, and separate conventional commits for
  completed tasks.

## Task 1: Public ATS feed adapters

Add adapters and fixture tests for Personio XML, softgarden JSON, d.vinci JSON/XML, onlyfy/Prescreen,
Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee XML, Workday, and the existing
SuccessFactors XML pattern.
Each adapter must return `RawJob` records, filter to Germany with conservative positive evidence,
mark capped or incomplete pagination as incomplete, and register under a persisted source kind.
Add shared ATS URL fingerprinting and safe tenant URL construction where useful, without changing
the existing built-in employer adapters' behavior.

## Task 2: Active BA employer discovery

Add a typed Arbeitsagentur Jobsuche client that calls `/pc/v6/jobs` with city, radius, publication
age, offer type, temporary-agency, and page/size filters. Parse employer name, job title, location,
postal code, reference number, and publication date into immutable signals, group duplicates by a
normalized employer identity, and expose total active jobs, recent jobs, and distinct locations.
Persist the signal snapshot and hiring-activity fields needed to rank candidates. Add a bounded
management command/service entry point that can be run without creating account monitoring targets.

## Task 3: Common Crawl tenant discovery and employer resolution

Add a Common Crawl index client that discovers recognized ATS tenants from the current or configured
snapshot using bounded wildcard queries and returns deduplicated tenant descriptors. Validate each
tenant's public feed before it becomes a source candidate. Add an employer resolver interface with
the existing OSM provider as a safe fallback and an optional Overture-backed resolver boundary, so
BA employer names can be mapped to public domains without making Overture a required dependency.

## Task 4: Reverse discovery orchestration and integration

Add a discovery orchestrator and scheduled/management entry points in this order: live ATS tenant
feeds, BA employer signals, domain resolution, career/ATS fingerprinting, then collection through
the existing source lifecycle. Integrate city monitoring so it uses BA signals before the old OSM
website sweep and only uses OSM as fallback. Update settings, deployment documentation, activity
metrics, and focused integration tests. Keep direct domain setup and current source controls intact.
