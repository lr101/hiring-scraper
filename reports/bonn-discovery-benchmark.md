# Bonn discovery investigation — 102 employers

The six-page pipeline now produces job rows for **12 of 102 employers**, compared with **10 before the changes**, using **454 rather than 458 page fetches**. The paired output increases from 190 to 271 rows: **75 Telekom rows and six walterservices rows**. These are pipeline counts, not audited vacancy counts or Bonn-only job totals. In particular, the original 190 includes an unverified group-wide Personio import of 134 rows for Canini.

DHL needs two separate interventions: its mapped company lacks a website, and the diagnostic `.com` homepage cannot pass robots-policy retrieval. Accessible official German/career entry points do work with the new parser. The Bonn career page exposes **10 Bonn jobs** in its embedded first-page selection; a six-page crawl from that page collects 39 jobs across several cities. Telekom's explicit Bonn search exposes 100 rows, of which **60 match the seed's employer brand and list Bonn**; 40 other group employers are excluded. These targeted entry-point trials are separate from the frozen cohort and do not inflate its headline improvement.

## Sample and evidence

Baseline: local commit `0615a3c554b371e94f1e76387972ecaaf7db933b`. Public responses were captured on 2026-10-08. The scope is a 12 km circle around Bonn, 50.7374 / 7.0982.

The public Overpass `office`, `craft`, and `industrial` query returned 1,670 objects and 1,668 usable candidates. There are 960 objects with direct website tags and 202 with a Wikidata reference. After excluding DHL/Telekom and deduplicating website hosts, 884 candidates were eligible. The additional 100 were selected by ascending SHA-256 of OSM source ID **before career outcomes were known**. DHL and Telekom are forced diagnostic seeds. The cohort includes businesses, trades, associations, and public employers, and represents employers with mapped websites, not all Bonn companies. Shared-domain legal entities were not merged; host deduplication applies only to benchmark sampling.

The [frozen cohort](../data/bonn-discovery/cohort.json) names all 102 examples and their source records. [Scope and source diagnostics](../data/bonn-discovery/scope.json) contain the geographic bounds and source hash. The [result artifact](bonn-discovery-benchmark.json) records all company outcomes, job identities, source employers, page paths, fetch states, partial-scan flags, and response hashes/timestamps. Raw HTTP bodies and full run output remain ignored local inputs. The acquisition log did not retain which of the two POST Overpass hosts succeeded; the source snapshot is identified by its payload hash and OSM timestamp. GET attempts had returned 504; a later named diagnostic query returned 500 and was not treated as evidence of absence.

## Paired cohort results

| Configuration | Employers with job rows | Career content without rows | Unresolved | Job rows | Pages fetched |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline, six pages | 10 | 34 | 58 | 190 | 458 |
| Baseline, twelve pages | 13 | 31 | 58 | 291 | 632 |
| Retained changes, six pages | 12 | 32 | 58 | 271 | 454 |

The twelve-page run adds 101 rows with 174 additional page fetches. BWI grows 37→71, Faßbender Tenten 6→10, technisch-mathematische studiengesellschaft 4→6, and Neugart 3→20. Kampmeyer adds one row, Ascio ten, and SAP Leanix 33. Those are raw discovery gains; the Ascio/SAP group-board attribution and generic HTML identities need an independent audit. Doubling the page cap leaves the same 58 companies unresolved. It was retained as an experiment, **not as a new production default**.

At the homepage boundary, 80 baseline seeds returned usable pages, 18 had unavailable robots policy, three returned HTTP errors, and one had a network error. Only 36 of the 58 unresolved companies reached a usable homepage. Increasing career depth cannot fix the other 22.

## DHL: company, homepage, navigation, and extraction failures

The mapped [DHL Global Forwarding record](https://www.openstreetmap.org/way/33723393) has an operator name but no website or Wikidata reference. The office query did not produce a directly usable DHL headquarters website. Conversely, [Telekom's headquarters node](https://www.openstreetmap.org/node/11782427334) has `https://www.telekom.com/de`. This explains a company/homepage-stage difference before any career parsing happens. It does not prove that DHL is absent from all OSM tags or outside this query's coverage.

`https://www.dhl.com/robots.txt` timed out; the bounded client consequently reported `robots_unavailable` and did not fetch the homepage. The corporate `group.dhl.com` policy also remained unavailable. Neither was bypassed. The [official German homepage](https://www.dhl.de/) was accessible and linked to the [DHL careers site](https://careers.dhl.com/eu/de/).

Three further defects were measured on accessible sources:

1. The careers home page's visible navigation sent the six-page crawler to Trier, Aachen, Erfurt, Kassel, and Krefeld. Its Schema.org `WebSite/SearchAction` supplied the real search-results route, which the old discovery code ignored. The new code promotes an explicit same-origin job-search target, handles `urlTemplate`, and refuses unrelated/external targets.
2. The public search HTML contains literal `phApp.ddo.eagerLoadRefineSearch.data.jobs` records. The generic framework extractor did not inspect this observed Phenom contract. A narrow parser now reads JSON literals without executing JavaScript, uses the publisher's same-origin base URL and explicit job-route template, preserves stable posting IDs, and excludes private/internal/inactive/initiative rows. Machine-generated skills are not imported as qualifications. Embedded detail `structureData/JobPosting` can supply the source employer and description.
3. The German homepage and career site use different domains. Nested search pages lost the verified career gateway's provenance. Inherited trust now requires an explicitly linked gateway with the employer brand in its **hostname**, and an exact same-host parent chain. A brand in a shared portal's path/title cannot authorize its global listing. Unexpanded cookie-template links are discarded before spending crawl pages.

The ordinary search response contained ten records out of 9,923 advertised hits. The [Bonn page](https://careers.dhl.com/eu/de/jobs-in-bonn) contained ten out of 126 hits. Neither is complete. The parser leaves all Phenom selections/details incomplete, even if the selection is empty; unknown pagination and partial records cannot close unseen jobs. A fetched software-developer detail established the constructed public route and exposed the same stable posting ID, explicit Bonn location, and `DHL Sorting Center GmbH` employer. The alternative homepage recovered 29 multi-city rows; the Bonn-page crawl recovered 39, including ten explicitly located in Bonn.

**Remaining DHL work:** fill the mapped company's missing website through verified official identity evidence, and support recovery from an already-known homepage whose robots policy is unavailable. Optional Tavily/Brave credentials were absent, so the actual configured search fallback was not exercised. No hardcoded DHL domain substitution or company rename was introduced. The original `.com` seed remains unresolved in the fixed-cohort test.

## Telekom: public search data, not an HTML job feed

The baseline follows `telekom.com` → `careers.telekom.com/de` → `/de/jobs`, but the returned job-search HTML contains no postings. Inspecting its public JavaScript revealed the same-origin `/api/jobs-proxy/keyword_search` GET contract. The wider `/search` route also accepts GET and returns ten rows, but its observed response has no verified total or continuation contract. The chatbot endpoint and applicant/submission APIs were not used.

The unfiltered German keyword response exposes 100 records, including a source employer, native ID, title, city, and location. The new exact-host adapter recognizes canonical German/English jobs routes and preserves an explicit `location` filter. It reconstructs public detail URLs with the site's own slug rule, preserves employer names, ignores machine-generated skill lists, and rejects malformed payloads. Language aliases are one discovery source, avoiding duplicate group feeds. Native IDs can carry a provider prefix: the Bonn capture includes `teamtailor_8078885`; requiring numeric IDs incorrectly rejected the entire batch and was corrected with a regression test.

The ordinary cohort entry point parses 100 rows and accepts 75 under the existing employer-brand check, rejecting 25 other employers. Only four of those 75 list Bonn. The explicit [Bonn jobs route](https://careers.telekom.com/de/jobs?location=Bonn) returns 100 Bonn-linked records: 60 pass the employer check, 40 do not. Accepted source employer names include Telekom subsidiaries and are retained in metadata; this is brand-level attribution, not a claim that every role belongs to Deutsche Telekom AG. T-Systems, BUYIN, and other nonmatching employers are excluded rather than silently folded into the seed.

Five sampled Bonn `/search` detail URLs returned actual structured job postings. Their source employers included Deutsche Telekom IT, Telekom Ausbildung, T-Systems, and PASM. This validates public permalink construction while demonstrating why a group feed must not automatically attribute every row to the seed employer. The keyword response and detail pilot are different selections; five detail checks do not independently audit all 60/75 keyword records.

The source always remains incomplete. Scheduled refresh applies the same employer filter as initial discovery. An isolated SQLite regression proves that refresh rejects an unrelated employer and preserves an unseen existing job's active state and missing-scan counter.

## Additional failure patterns and options

| Example | Observed problem / option | Outcome |
| --- | --- | --- |
| walterservices | Softgarden login/signup routes consumed board slots; native-looking details were skipped after the three-board cap; `/job/<number>/<title>` missed the HTML parser | Six rendered job links recovered. Numeric route identity merges card/detail/schema variants. German titles need no English role keyword. Sources remain incomplete. |
| Canini Dentallabor | A company link to one Personio job caused import of the full `zahneinsgmbh` tenant | 134 imported rows remain unverified for this specific employer. Flagged for employer-scoped feed handling; not counted as 134 audited vacancies. No automatic company merge or destructive migration. |
| Fraunhofer IAIS | `Stellenangebote für Studentische Hilfskräfte` became a job row | A listing heading can masquerade as a vacancy. Needs stronger posting-identity validation; retained as a reported baseline accuracy problem. |
| terrestris | The `junior-dev` page contains a Lorem ipsum block; page-wide placeholder classification suppresses all extraction | Needs section-level examination. The English B.Sc. page also contains placeholder content; dropping the placeholder check wholesale would be unsafe. |
| BWI | Department-filter URLs monopolize the small crawl allowance | Twelve pages increased rows, but did not establish an exhaustive inventory. Prefer structured listing/pagination evidence over blanket budget growth. |
| Neugart / SAP Leanix / Ascio | Useful sources sit deeper in navigation | Twelve-page control recovers more rows; source/employer and duplicate audits remain necessary. |
| Lena’s Raum;Bettinas ARTelier | An OSM website value concatenates two sites with a semicolon | Malformed/stale homepage data needs its own ingestion repair; career parsing cannot repair identity evidence. |
| Telekom sitemap | Accessible static sitemap | Marketing/editorial routes did not supply the missing job inventory; no sitemap adapter retained. |
| Browser rendering | Attempted the available browser automation tool | Tool reported no automation host in this environment. No rendered-browser result or performance claim is made; no browser dependency added. |

The retained options add no runtime dependency, no JS execution, no POST search, and no new access-policy exemption. Feed-less ATS detail traversal remains within the existing page/depth/request limits. Source caps and unsupported pagination remain visible instead of being reported as complete scans.

## Reproduction and verification

```bash
uv sync --locked
# Each replay requires the ignored, hash-checked data/bonn-discovery/http captures.
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --variant baseline
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --variant expanded
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --variant candidate
.venv/bin/python -m experiments.bonn_discovery_benchmark diagnostics
.venv/bin/python -m experiments.bonn_discovery_benchmark persist
.venv/bin/python -m experiments.bonn_discovery_benchmark summarize
# Append --live to crawl/diagnostics to fetch missing captures; existing captures are reused.
.venv/bin/python -m unittest tests.test_bonn_discovery -v
.venv/bin/python -m unittest discover -s tests -q
```

The baseline loader reads the trusted local Git revision's ATS, HTML, page, and discovery modules, including their relative imports. Normal runs allow six pages, depth three, 45 requests per company, a 2,500-request shared cap, and shared one-second origin pacing. The expanded control allows twelve pages. Results are checkpointed by frozen cohort index. Offline replays do not make live requests or modify the capture cache. Each worker writes to a separate temporary capture directory; new live captures are archived only after all workers finish, preserving existing evidence. This experiment tests discovery/extraction and persistence, not matching precision, location-source market recall, or exhaustive inventories.

Regression tests were observed failing before fixes, including public Phenom extraction, SearchAction navigation, feed-less detail traversal, exact-host gateway inheritance, unrelated-employer refresh filtering, native ID prefixes, language aliases, and template links. Review found and corrected card/detail duplication, shared-path-tenant trust inheritance, and concurrent replay rewriting its own evidence. A 32-company/eight-worker regression checks that offline replay preserves capture bytes. The persistence pilot uses real SQLite and normal discovery/refresh code: DHL's 29/39 recovered multi-city rows remain present after an incomplete refresh; Telekom's explicit Bonn source remains at 60 rows after its employer-filtered refresh. This retention is protection against premature closure, not proof that every retained row was re-observed on the single refreshed page.

Next priority is verified company/homepage recovery and shared-tenant attribution, followed by proven pagination, precise vacancy identity, and passing regional context into supported feed discovery. Raising crawl limits alone leaves most unresolved cases untouched.
