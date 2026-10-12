# German job source candidates

This is a practical shortlist of public vacancy pages. Availability and page behavior may change.

## Feeds added to default discovery

The default feed scanner reads the maintained list in [`default_job_feeds.json`](../hiring_scraper/app/default_job_feeds.json). With the background feed worker enabled, each configured source is added idempotently and scanned as RSS / Atom. Feed windows are always treated as partial: a missing item does not close an older saved job.

| Source | Live probe (2026-10-11) | Default status |
|---|---|---|
| [JOBWOCHE Germany-wide RSS](https://www.jobwoche.de/jobs-abonnieren) | Official page invites subscribing in an RSS reader. The feed returned HTTP 200 (`application/rss+xml`) with 20 entries; sampled items expose job links, dates and an `Ort:` field. | Enabled. This is the broad Germany-wide default. |
| [Landeshauptstadt Stuttgart current vacancies](https://www.stuttgart.de/rathaus/karriere/fach-und-fuehrungskraefte/aktuelle-stellenangebote) | Official page links an RSS view. It returned HTTP 200 (`application/rss+xml`) with 47 postings. | Enabled as an employer-specific source with Stuttgart coordinates taken from the city-scoped career page. |

These feeds expose an intended RSS reader/embed interface. The scanner retains short feed descriptions and links back to each original vacancy; source content and reuse terms still belong to the publisher. They can be paused in **Job feeds**. Adding an entry to the JSON config creates a new feed on the next worker startup; existing pause state is retained.

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
| [Bundesagentur für Arbeit Jobsuche](https://www.arbeitsagentur.de/jobsuche/) | National jobs, training, and internships. | The repository includes an observed, bounded regional search adapter; it stops and records `bot_challenge` if BA returns a bot or CAPTCHA challenge. BA's [HR-BA-XML information](https://www.arbeitsagentur.de/unternehmen/arbeitskraefte/hr-ba-xml-schnittstelle) describes cooperation models for job boards. |
| [EURES](https://eures.europa.eu/) | EU-wide vacancy portal, including Germany. | The official privacy statement says activity is logged to detect crawlers and that misuse may trigger investigation. No public feed/API contract suitable for default collection was found; excluded from automatic scanning. |
| [EURAXESS jobs](https://euraxess.ec.europa.eu/jobs/search) | Research and academic vacancies across Europe, including Germany. | Specialist source with public search filters and results; recurring feed access and extraction have not been validated here. |
| [INTERAMT](https://karriere.interamt.de/) | Public-service vacancies. | Candidate source; search pages and pagination need a current, policy-aware probe before scheduling. |
| [Karriere Bund vacancies](https://www.karriere.bund.de/arbeiten-bei-uns/stellenangebote-bei-uns) | Federal administration roles across Germany. | Candidate source; confirm the current listing URL and its published interface before adding a recurring feed. |
| [jobvector](https://www.jobvector.de/jobs/) | Specialist jobs in engineering, IT, medicine, science, and related fields. | Listing pages are publicly readable in search indexing; this is not evidence of a stable feed contract or permission for automated reuse. Try only after checking the current page and terms. |

The Federal Employment Agency also points job seekers to Make it in Germany and EURES in its [official Germany job-search guidance](https://www.arbeitsagentur.de/arbeiten-in-deutschland/jobs-finden). EURAXESS describes its own portal as a research vacancy database, while INTERAMT is the public-service career portal.

## Employer career pages

The existing crawler has previously found accessible career pages for [TelemaxX](https://www.telemaxx.de/ueber-uns/karriere/berufserfahrene), [Ratiodata](https://karriere.ratiodata.de), [Unic](https://www.unic.com/de/jobs), [PTV Group](https://careers.ptvgroup.com/en/jobs/), [Open Experience](https://openexperience.de/de/karriere/), [PLANTA](https://www.planta.de/karriere/), and [Arnotec](https://job.arnotec.de/). These were observed in the dated [Karlsruhe evaluation](../reports/karlsruhe-evaluation.md); recheck them before relying on the same URLs today.

Company pages are usually the best attribution source. The [Landeshauptstadt Stuttgart current vacancies page](https://www.stuttgart.de/rathaus/karriere/fach-und-fuehrungskraefte/aktuelle-stellenangebote) publishes an RSS view and is included in the defaults. The built-in ATS adapters already cover common public boards such as Greenhouse, Lever, Personio, Ashby, and Recruitee. Career discovery now follows linked role families and category/search pages (including Ausbildung, dual study, working-student, and internship paths), up to 24 pages and depth 5 by default. It recognizes `/offer/...` vacancy links and can render up to four relevant JavaScript-driven listing pages per company; those rendered pages are saved as `browser_html` feeds so later scans use the same bounded renderer.

Atruvia illustrates the case: [its career page](https://atruvia.de/karriere) links to professionals, Ausbildung/dual-study, and student sections. The student portal publishes `/offer/...` job cards in its category pages, while some professional job categories need JavaScript rendering to reveal their vacancies. The crawler follows those linked sections and applies the same employer attribution and duplicate handling across the resulting feeds.

## Browser access and bot challenges

Playwright's headless browser can run page JavaScript and return the rendered HTML. It cannot guarantee access. A website can serve an explicit challenge to automated browsers; Cloudflare documents a `cf-mitigated: challenge` response header for this case. The scanner records a detected bot or CAPTCHA challenge as `bot_challenge` and stops that scan. It does not solve CAPTCHAs, rotate proxies, disguise the browser, or reuse human session cookies. See the official [Playwright Python installation guide](https://playwright.dev/python/docs/intro) and [Cloudflare challenge response guide](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).

An unfamiliar page that yields no recognized job records is marked incomplete unless it explicitly says that it has no open positions. This protects existing postings from being closed when a board changes layout or extraction needs a source-specific adapter.
