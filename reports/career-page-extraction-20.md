# Employer-hosted career page extraction

**Observed:** 2026-10-05 UTC  
**Sample:** the 20-company Karlsruhe IT follow-up cohort, replayed with the final parser against saved HTTP captures in `data/career-poc/run-22/`. The replay used 145 cached responses and made no network requests. It is a dated feasibility sample, not an estimate of national coverage.

## Outcome

Five ordinary employer-hosted career pages yielded **24 job-page records**. The existing structured ATS adapters still found one Greenhouse feed at IONOS with 37 jobs. The cohort ended with 10 unresolved companies, 4 with career content but no parsed jobs, 5 with page-extracted jobs, and 1 with a parsed structured feed. “Unresolved” means the bounded crawl could not verify a career page; it does not mean the company has no jobs.

| Company | Employer career page | Roles | Useful fields and limits |
|---|---|---:|---|
| [qwertiko GmbH](https://www.qwertiko.de/jobs/) | Company-hosted job listing | 2 | Same-site job links led to detail pages with descriptions and Karlsruhe context. No posted date or employment type was found. |
| [Tooltec IT GmbH](https://tooltec.de/karriere/) | Company-hosted career page | 2 | Vacancy headings and card text gave Karlsruhe, full-time/apprenticeship, on-site arrangement, and future start dates. The start date is preserved as raw metadata, not misreported as a posting date. |
| [heliopas.ai GmbH](https://heliopas.ai/jobs/) | “Offene Stellen” section | 7 | Role headings and descriptions were extracted. No location was present in those role blocks, so these records do not match Karlsruhe-radius searches. Currentness is not guaranteed by the page structure. |
| [Easy Smart Grid GmbH](https://www.easysg.de/about-us/careers/) | Career page | 1 | A working-student/master-thesis role included a description, Karlsruhe and Constance locations, and working-student type. |
| [HS Analysis GmbH](https://hs-analysis.com/careers/) | “Open Positions” section | 12 | Role headings, descriptions, Karlsruhe, and some full-time labels were extracted. No posted dates were exposed. |

Across the 24 HTML records, 20 have descriptions, 17 have a location, 13 have an employment type, 2 have a work arrangement, and none has a posted date or salary. Seven heliopas.ai roles have no location. A page being labelled “open positions” is only evidence that the employer presented those roles as open at capture time; the sample has no reliable age or closing-date signal.

## Techniques tested

The extractor uses the standard-library HTML parser and combines a few deliberately separate signals:

- Schema.org JSON-LD `JobPosting`, with title, canonical job URL, description, date posted, employment type, location and remote hints mapped into the common job shape.
- Embedded JSON arrays in `application/json` and common Next.js data, accepting job-like objects only when they have a title and a usable URL.
- Semantic career-page structure: headings inside an explicit “Open Positions” or “Offene Stellen” section, plus same-site links whose URL is clearly a job-detail route.
- Detail-page context: an H1 and job metadata can complete a role found from a listing link. Boilerplate footer text alone is not used to infer the job type.

In this cohort, the actual successful parse methods were 20 `open_positions_heading` records, 2 `html_job_detail` records, and 2 `html_role_heading` records. The JSON-LD and embedded-JSON paths are covered by fixtures and unit tests, but neither produced a record in this particular sample. This is a useful distinction: those standards-based paths are ready for matching sites, while the five-company yield here came from visible static HTML.

The parser also treats generic `ItemList` entries as **unconfirmed role hints**, not live jobs. For example, an Awesome page included role-like entries whose destinations were blog content; importing them as postings would create false positives. Emmtrix’s explicit “no open positions” text similarly remains a career-content result with no jobs. News/blog URLs and placeholder career routes are excluded from job extraction.

## App integration and remaining limits

The five page sources are seeded as `html_jobs` records and use the career page itself as the recurring scan URL. The worker fetches these through the robots-aware website client, extracts current listings, and stores them alongside structured ATS jobs. They are not labelled as standardized vendor feeds. In the company view, filters separate companies with a discovered domain, a career page, a structured job feed, or active extracted jobs. In the jobs view, each row shows the employer name/domain and a link to the site where the job was found.

Static HTML is the main practical win here: it needs no vendor API and returned useful role data for five sites. It is also less reliable than an ATS feed. A client-rendered page without job data in its initial HTML remains unsupported; there is no browser automation in this pass. Role headings can outlive active vacancies, many pages omit posting dates, the location dictionary is intentionally limited, and the current 20-company cohort is heavily biased toward Karlsruhe IT firms. Unknown locations are retained as unknown rather than assigned to the company’s office, so they do not appear as local jobs.

The seed snapshot now has **7 structured feed records with 93 jobs** and **5 HTML career-page sources with 24 extracted roles**, across 11 OSM companies. The 24 page records and their extracted fields are in `fixtures/karlsruhe-career-enrichment.json`; raw page captures remain in the dated POC run and are not needed by the runtime app.

## Reproduction

Replay the parser against captured bodies without additional requests:

```bash
python3 -m hiring_scraper --seeds data/career-poc/seeds-karlsruhe-it-followup-20.json \
  --out data/career-poc/new-run --cache-from data/career-poc/run-20/http
```

Use a new output directory for every run. `data/career-poc/run-22/summary.json` records the dated replay totals.
