# German job source candidates

This is a practical shortlist of public vacancy pages. Availability and page behavior may change.

## Feeds added to default discovery

The default feed scanner reads the maintained list in [`default_job_feeds.json`](../hiring_scraper/app/default_job_feeds.json). With the background feed worker enabled, each configured source is added idempotently and scanned as RSS / Atom. Feed windows are always treated as partial: a missing item does not close an older saved job.

| Source | Live probe (2026-10-11) | Default status |
|---|---|---|
| [JOBWOCHE Germany-wide RSS](https://www.jobwoche.de/jobs-abonnieren) | Official page invites subscribing in an RSS reader. The feed returned HTTP 200 (`application/rss+xml`) with 20 entries; sampled items expose job links, dates and an `Ort:` field. | Enabled. This is the broad Germany-wide default. |
| [Landeshauptstadt Stuttgart current vacancies](https://www.stuttgart.de/rathaus/karriere/fach-und-fuehrungskraefte/aktuelle-stellenangebote) | Official page links an RSS view. It returned HTTP 200 (`application/rss+xml`) with 47 postings. | Enabled as an employer-specific source with Stuttgart coordinates taken from the city-scoped career page. |

These feeds expose an intended RSS reader/embed interface. The scanner retains short feed descriptions and links back to each original vacancy; source content and reuse terms still belong to the publisher. They can be paused in **Job feeds**. Adding an entry to the JSON config creates a new feed on the next worker startup; existing pause state is retained.

## Feed formats and ATS adapters

RSS and Atom are both handled by the same XML feed parser. Other useful public formats in Germany are provider-specific XML and JSON APIs: there is no single cross-company job-feed standard. These adapters are discovered from a company's career URL and then saved as managed job feeds; they are not entries in the two-source RSS defaults.

| Format/provider | Public endpoint pattern | German example to inspect or test |
|---|---|---|
| Greenhouse JSON | `boards-api.greenhouse.io/v1/boards/{tenant}/jobs` | [IONOS board](https://job-boards.eu.greenhouse.io/ionos), also used by the existing Greenhouse tests. |
| Lever JSON | `api.lever.co/v0/postings/{site}` or the EU API host | [Creatio Germany-filtered board](https://jobs.eu.lever.co/creatio?location=Germany); the generic reader retains its existing ten-page safety cap. |
| Personio XML | `{tenant}.jobs.personio.de/xml` | [cisbox career page](https://cisbox-gmbh.jobs.personio.de/); its public XML feed is `https://cisbox-gmbh.jobs.personio.de/xml`. The XML format is documented by [Personio](https://developer.personio.de/docs/retrieving-open-job-positions). |
| Recruitee XML | `{tenant}.recruitee.com/api/feeds/offers.xml` | [virtual7 jobs](https://virtual7jobs.recruitee.com/) includes Karlsruhe vacancies; its public XML endpoint follows this pattern. The deterministic parser fixture and endpoint allowlist are in [`test_recruitee.py`](../tests/test_recruitee.py). |
| Ashby JSON | `api.ashbyhq.com/posting-api/job-board/{board}` | [DeepL board](https://jobs.ashbyhq.com/DeepL); the public posting API is documented by [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api). |
| SmartRecruiters JSON | `api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings?limit=100&offset=0&destination=PUBLIC` | [rheindata GmbH board](https://careers.smartrecruiters.com/RheindataGmbH). The new fixture mirrors its public posting shape in [`test_smartrecruiters.py`](../tests/test_smartrecruiters.py). |
| Telekom JSON | Observed first-party `/api/jobs-proxy/keyword_search` | [Telekom careers](https://careers.telekom.com/de/jobs); this adapter uses a bounded observed endpoint, not a general Telekom API guarantee. |

Greenhouse, Lever, Personio, Recruitee, Ashby, and SmartRecruiters have feed parsers in this repository. A read-only probe on 2026-10-12 returned HTTP 200 and parsed 40 Greenhouse, 98 Lever, 11 Personio, 9 Recruitee, 7 Ashby, and 1 SmartRecruiters posting from the examples above. These counts can change. SmartRecruiters exposes the public postings endpoint and supports `limit`/`offset`; the adapter explicitly requests `destination=PUBLIC`, caps scans at eight pages, and marks a capped or interrupted scan incomplete. It does not request details from per-job APIs. A small read-only SmartRecruiters smoke request is:

```bash
curl -fsS 'https://api.smartrecruiters.com/v1/companies/RheindataGmbH/postings?limit=100&offset=0&destination=PUBLIC'
```

Run `mise exec -- uv run python -m unittest tests.test_smartrecruiters` for deterministic adapter, normalization, pagination, and endpoint-allowlist coverage. A tenant with zero public jobs is still a valid board; it simply has no row to use for a visual smoke test.

Workday, SAP SuccessFactors, Softgarden, Helix, Onlyfy, and some custom career platforms are recognized during discovery but do not currently have a generic feed adapter here. Their public interfaces vary by employer or require authenticated/customer-specific contracts, so they remain regular career pages until a safe public contract and representative German fixture are verified. A public HTML page or Schema.org `JobPosting` remains the fallback when it provides parseable jobs.

## Other accessible feed candidates

| Source | Live probe (2026-10-11) | Why it is not enabled by default |
|---|---|---|
| [Stellenmarkt.de RSS](https://www.stellenmarkt.de/rss.php) | The publisher offers 15 category feeds for embedding; the IT feed returned HTTP 200 (`application/xml`) with 10 entries. | The sampled listings did not carry structured locations, so they cannot safely enter the app's location-scoped default results. It can be added manually as an RSS source. |
| [Jobcafe RSS](https://www.jobcafe.de/F=rss_feed) | The publisher lists category feeds for newsreaders; its IT feed returned HTTP 200 (`application/atom+xml`) with 3 entries. | The sample's `updated` timestamps were from 2021, so it was excluded as stale. |
| [JOBWOCHE regional feeds](https://www.jobwoche.de/jobs-abonnieren) | `/rss/karlsruhe` and `/rss/berlin` returned valid RSS with zero entries; `/rss/muenchen` returned 404. | The regional endpoints were not reliably populated in this probe. Use the Germany-wide feed instead. |
| [BiblioJobs RSS](https://bibliojobs.eu/faq/) | The documented feed host failed TLS certificate verification during the live probe. | Excluded until the publisher fixes the certificate. |
| [EURES](https://eures.europa.eu/) | Its privacy statement says portal use is logged to detect crawlers and warns about misuse. | No public feed/API contract suitable for default automated collection was found. |

## Broad and specialist boards

| Source | Coverage | Current integration notes |
|---|---|---|
| [Make it in Germany Jobbörse](https://www.make-it-in-germany.com/de/arbeiten-in-deutschland/jobboerse/job-listings) | Federal government portal for qualified professionals from abroad. | Its official publishing guide says listings are submitted through BA, so expect overlap. Direct HTTP returned no job rows here; headless Chromium received an HTTP 200 challenge page. The scanner records `bot_challenge` and stops the scan. |
| [Bundesagentur für Arbeit Jobsuche](https://www.arbeitsagentur.de/jobsuche/) | National jobs, training, and internships. | Selectable as an observed, bounded regional adapter in the regular feed form. It stops and records `bot_challenge` if BA returns a bot or CAPTCHA challenge. BA's [HR-BA-XML information](https://www.arbeitsagentur.de/unternehmen/arbeitskraefte/hr-ba-xml-schnittstelle) describes cooperation models for job boards. |
| [EURES](https://eures.europa.eu/) | EU-wide vacancy portal, including Germany. | The official privacy statement says activity is logged to detect crawlers and that misuse may trigger investigation. No public feed/API contract suitable for default collection was found; excluded from automatic scanning. |
| [EURAXESS jobs](https://euraxess.ec.europa.eu/jobs/search) | Research and academic vacancies across Europe, including Germany. | Specialist source with public search filters and results; recurring feed access and extraction have not been validated here. |
| [INTERAMT](https://karriere.interamt.de/) | Public-service vacancies. | Candidate source; search pages and pagination need a current, policy-aware probe before scheduling. |
| [Karriere Bund vacancies](https://www.karriere.bund.de/arbeiten-bei-uns/stellenangebote-bei-uns) | Federal administration roles across Germany. | Candidate source; confirm the current listing URL and its published interface before adding a recurring feed. |
| [jobvector](https://www.jobvector.de/jobs/) | Specialist jobs in engineering, IT, medicine, science, and related fields. | Listing pages are publicly readable in search indexing; this is not evidence of a stable feed contract or permission for automated reuse. Try only after checking the current page and terms. |

The Federal Employment Agency also points job seekers to Make it in Germany and EURES in its [official Germany job-search guidance](https://www.arbeitsagentur.de/arbeiten-in-deutschland/jobs-finden). EURAXESS describes its own portal as a research vacancy database, while INTERAMT is the public-service career portal.

### Add a Bundesagentur für Arbeit search

The BA Jobsuche is a query-based search, not a reusable RSS/Atom feed. In **Job feeds**, enter the public Jobsuche URL and select **Agentur für Arbeit · scheduled search** in the regular **Page type** field. You do not enter a separate location filter. Its scheduled scans inherit the discovery scope: a Germany-wide search omits `wo` and `umkreis`; a saved city search such as Karlsruhe passes the city and its configured radius. Job terms are optional. The adapter uses the shared feed scanner and can be paused, edited, or scanned on demand with other sources. Leaving job terms blank uses the built-in German and English role families. A bot or CAPTCHA challenge is shown as the scan reason. Leave the company/source name blank; the page labels the source **Agentur für Arbeit**.

The same opt-in capture is available from the command line. From the project root, use a **new output directory** for each capture:

```bash
mise exec -- uv run python -m hiring_scraper.app.regional acquire \
  --city Karlsruhe --radius-km 35 \
  --term 'Softwareentwickler' --term 'Data Engineer' \
  --out /tmp/ba-karlsruhe-capture-1 \
  --import
```

Omit `--term` to use the built-in German and English role families. You can also set `--profile-id ID` for a saved profile or `--profile-json path.json` for a JSON object containing `search_area.city`, `search_area.radius_km`, and optionally `search_area.terms`. Repeating `--term` replaces the default role families. The default capture is bounded to three result pages per term, 100 detail reads, 300 total requests, one-second pacing, and two retries; these limits can be adjusted with `--max-pages`, `--max-details`, `--max-requests`, `--delay`, and `--retries`.

`--import` imports the resulting `snapshot.json` into the app database; without it, the JSON capture remains on disk and can later be loaded with `mise exec -- uv run python -m hiring_scraper.app.regional import /path/to/snapshot.json`. Imports are idempotent and do not close a job merely because it is absent from a later selected search. The CLI is a manual capture option; for recurring scans, add the adapter through **Job feeds**. A bot or CAPTCHA challenge stops acquisition and is recorded as the reason.

BA's [HR-BA-XML interface](https://www.arbeitsagentur.de/unternehmen/arbeitskraefte/hr-ba-xml-schnittstelle) is for submitting vacancies into BA from employer/job-board systems under its integration requirements; it is not a public read feed for arbitrary job searches. Use the **Job feeds** adapter or the CLI capture above to fetch a selected BA search.

## Employer career pages

The existing crawler has previously found accessible career pages for [TelemaxX](https://www.telemaxx.de/ueber-uns/karriere/berufserfahrene), [Ratiodata](https://karriere.ratiodata.de), [Unic](https://www.unic.com/de/jobs), [PTV Group](https://careers.ptvgroup.com/en/jobs/), [Open Experience](https://openexperience.de/de/karriere/), [PLANTA](https://www.planta.de/karriere/), and [Arnotec](https://job.arnotec.de/). These were observed in the dated [Karlsruhe evaluation](../reports/karlsruhe-evaluation.md); recheck them before relying on the same URLs today.

Company pages are usually the best attribution source. The [Landeshauptstadt Stuttgart current vacancies page](https://www.stuttgart.de/rathaus/karriere/fach-und-fuehrungskraefte/aktuelle-stellenangebote) publishes an RSS view and is included in the defaults. The built-in ATS adapters cover public Greenhouse, Lever, Personio, Ashby, Recruitee, and SmartRecruiters boards. Career discovery now follows linked role families and category/search pages (including Ausbildung, dual study, working-student, and internship paths), up to 24 pages and depth 5 by default. It recognizes `/offer/...` vacancy links and can render up to four relevant JavaScript-driven listing pages per company; those rendered pages are saved as `browser_html` feeds so later scans use the same bounded renderer.

Atruvia illustrates the case: [its career page](https://atruvia.de/karriere) links to professionals, Ausbildung/dual-study, and student sections. The student portal publishes `/offer/...` job cards in its category pages, while some professional job categories need JavaScript rendering to reveal their vacancies. The crawler follows those linked sections and applies the same employer attribution and duplicate handling across the resulting feeds.

## Browser access and bot challenges

Playwright's headless browser can run page JavaScript and return the rendered HTML. It cannot guarantee access. A website can serve an explicit challenge to automated browsers; Cloudflare documents a `cf-mitigated: challenge` response header for this case. The scanner records a detected bot or CAPTCHA challenge as `bot_challenge` and stops that scan. It does not solve CAPTCHAs, rotate proxies, disguise the browser, or reuse human session cookies. See the official [Playwright Python installation guide](https://playwright.dev/python/docs/intro) and [Cloudflare challenge response guide](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).

An unfamiliar page that yields no recognized job records is marked incomplete unless it explicitly says that it has no open positions. This protects existing postings from being closed when a board changes layout or extraction needs a source-specific adapter.
