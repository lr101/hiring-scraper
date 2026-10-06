# JavaScript career extraction PoC

The saved-capture PoC found one safe incremental path: explicit, server-rendered
job cards from a framework-backed board. The default worker still does not run
JavaScript or make a new request. `extract_html_jobs` now recognizes only an
anchor marked `data-guide-id="joblist-card"`, reads its nested title, location,
and tag fields, and retains its same-page query-string detail URL. Ordinary
anchors and framework scripts continue through the existing conservative paths.

## Repeatable command

```bash
.venv/bin/python experiments/javascript_career_extraction_poc.py --run-dir data/career-poc/run-31 --sample-size 250
```

The command selects one context per capture from career/job-like successful HTML
pages without an ATS marker or `JobPosting`; it orders them by
`SHA-256(page URL + newline + capture ID)` and takes the first 250. It reads
only those 250 bodies after scanning lightweight capture metadata.

## Results

`run-31` contains 1,728 captures matching the sample eligibility rule. The
250-capture slice contained 7 `__NEXT_DATA__`, 2 Next Flight, 9 Nuxt, 70
API/GraphQL/JSON-looking reference, and 71 job-term-in-script markers. Markers
were leads, not jobs: the existing static JSON-state parser recovered zero
`embedded_json` jobs in the slice.

The one useful framework-bearing lead was Wibu Systems:

| Source | Saved capture / bot access | Existing result | New result | Increment |
| --- | --- | ---: | ---: | ---: |
| `https://jobs.wibu.com/en` | capture `cf39ff8b1927aa89fb50`; 200 HTML, 472,810 bytes; `robots.txt` capture `b96ca803147990271f9f` was 200 `text/plain` | 2 synthetic role headings | 10 explicit current cards | 8 unique postings |

The capture is linked to the employer's `https://www.wibu.com/` site and the
existing `trusted_html_jobs` check returns `first_party`. All ten rows have a
distinct `?id=` detail identity, a non-placeholder title, and the card's
explicit Karlsruhe location. The saved card list has no closed/no-open-jobs
signal. The two earlier student headings match two card titles and are
suppressed by the structured-card parser, so the deduplicated increment is
eight. `Initiativbewerbung (m/w/d)` is retained because it is a separately
identified, active card; its type comes from the explicit `Full time` tag,
rather than incidental text in the card description.

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
contracts; other framework markers remain evidence only.
