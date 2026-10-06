# JavaScript career extraction PoC

The saved-capture PoC found one safe incremental path: explicit, server-rendered
job cards from a framework-backed board. The default worker still does not run
JavaScript or make a new request. `extract_html_jobs` recognizes only a
same-origin anchor marked `data-guide-id="joblist-card"`, rejects hidden,
disabled, closed, and template cards, and preserves its normalized query-string
detail identity. Ordinary anchors and framework scripts continue through the
existing conservative paths.

## Repeatable command

```bash
.venv/bin/python experiments/javascript_career_extraction_poc.py --run-dir data/career-poc/run-31 --sample-size 250
```

The command writes the compact replay artifact at
`data/career-poc/js-extraction-poc-2026-10/replay.json`. It selects one context
per capture from saved page metadata for career/job-like successful HTML pages
without an ATS marker and with `jobposting_count=0`; it orders them by
`SHA-256(page URL + newline + capture ID)` and takes the first 250. It loads
only those 250 bodies.

## Results

`run-31` contains 1,775 captures matching the metadata eligibility rule. The
250-capture slice contained 7 `__NEXT_DATA__`, 2 Next Flight, 9 Nuxt, 70
API/GraphQL/JSON-looking references, and 72 job-term-in-script markers. Markers
were leads, not jobs: generic framework JSON recovered zero active jobs and
zero unconfirmed candidates in this slice. Generic state arrays are retained as
unconfirmed candidates only when they are same-origin and neither closed nor
template data; only JSON-LD `JobPosting` remains an active JSON job contract.

The one useful framework-bearing lead was Wibu Systems:

| Source | Saved capture / bot access | Existing result | New result | Increment |
| --- | --- | ---: | ---: | ---: |
| `https://jobs.wibu.com/en` | capture `cf39ff8b1927aa89fb50`; 200 HTML, 472,810 bytes; `robots.txt` capture `b96ca803147990271f9f` was 200 `text/plain` | 2 synthetic role headings | 9 active cards plus 1 unconfirmed initiative route | 9 new stable detail identities; 7 roles without a prior title hint |

The capture is linked to the employer's `https://www.wibu.com/` site and the
existing `trusted_html_jobs` check returns `first_party`. The active rows have
distinct normalized `?id=` destination identities, non-placeholder titles, and
the card's explicit Karlsruhe location. The saved card list has no
closed/no-open-jobs signal. The two earlier student headings overlap two card
titles and are suppressed only as synthetic hints; they are not used as posting
identity. Therefore all nine card URLs are new stable detail identities; seven
have no prior title/location role hint and two replace synthetic heading hints.
If a downstream display intentionally title-deduplicates them, that is a
separate seven-role presentation metric, not posting identity deduplication.
`Initiativbewerbung (m/w/d)`
is an unsolicited application route and is retained only as an unconfirmed
role candidate. The same treatment applies to narrow generic English labels
such as `Initiative Application`, `Unsolicited Application`, `General
Application`, and talent-pool/community routes; a specific role such as
`Talent Acquisition Manager` remains active. The replay artifact records every
active Wibu row, its identity key, source access metadata, trust result,
baseline count, title overlap, and new-row count.

## Exclusions and competing approaches

Next Flight payloads were inspected only as serialized text. Their useful Wibu
page content was already server-rendered as the explicit cards above; parsing
the opaque RSC stream would add complexity without an incremental posting.
Nuxt and Next JSON state produced no accepted jobs in the deterministic slice.
The 70 endpoint-looking strings were not fetched: they do not establish a
documented, same-origin read-only job endpoint, and following arbitrary code
or URL hints would exceed the worker's safe request policy. Consequently this
PoC made zero same-origin endpoint requests.

No browser PoC ran: Playwright and Selenium are absent. Installing a browser
was not justified after the static page yielded the validated increment.

The parser is intentionally narrow. It costs one additional walk over the
already parsed HTML tree and adds no network, JavaScript engine, browser, or
third-party dependency. Generalization is limited to sites that expose the
observed `joblist-card`, `joblist-card-title`, and optional location/tag
contracts; other framework markers remain evidence only. Cards are rejected
when their class names identify them as closed or archived, or when explicit
`data-disabled`/`data-template` flags are true. False flag values do not hide a
live card.
