# Bonn public-source follow-up — 102 employers

The production workers now skip robots.txt by default, with transparent identification, per-origin pacing, request caps, and HTTP 429/503 cooldowns. The same frozen 102-company sample yields **201 rows across 13 employers at the new twelve-page default**. The corrected six-page run yields **141 rows across 13 employers**, and twenty-four pages yields **217 rows across the same 13 employers**. The previous six-page headline was 271 rows across 12 employers, but included 134 group-wide Canini rows; fresh discovery now keeps its three explicitly linked postings. Unrelated Reltio/Tucows feeds found by deeper crawling are rejected before persistence. These are pipeline rows, not independently audited vacancies or Bonn-only jobs.

No anti-bot challenge, CAPTCHA, login, or access-control bypass was attempted. No proxy rotation, impersonating user agent, private applicant API, or stealth browser was introduced. Network timeouts and HTTP errors remain visible outcomes. The production code changed; no deployment was performed.

## Measurement

The cohort is unchanged: DHL, Telekom, and 100 additional distinct website hosts selected before career outcomes from the 12 km Bonn OSM sample. See the [original benchmark](bonn-discovery-benchmark.md) and [frozen cohort](../data/bonn-public/cohort.json). New public responses were captured on 2026-10-09; successful 2026-10-08 captures were reused. Previously failed network/HTTP responses were omitted from the new input cache to permit one fresh bounded attempt; established redirects and 404 responses were retained. Thus comparisons with the previous benchmark also include capture freshness, not just policy changes.

| Pipeline | Employers with rows | Extracted rows | Pages fetched | Unresolved companies |
| --- | ---: | ---: | ---: | ---: |
| Original baseline, 6 pages, robots checks | 10 | 190 | 458 | 58 |
| Previous retained implementation, 6 pages, robots checks | 12 | 271 | 454 | 58 |
| Current parser, 6 pages, no robots checks | 13 | 141 | 492 | 56 |
| Current parser, 12 pages, no robots checks | 13 | 201 | 689 | 56 |
| Current parser, 24 pages, no robots checks | 13 | 217 | 870 | 56 |

The current three variants use the same hash-checked cache and code, with no live requests during replay. The twelve-to-twenty-four-page gain is nine BWI and seven Faßbender Tenten rows, costing 181 additional page fetches and recovering no further employers. Twelve pages is the retained default; twenty-four is configurable. This tradeoff is measured, not a claim of exhaustive retrieval.

Usable homepages increase from 80 to **89 of 102**. The remaining roots are nine network errors and four HTTP errors. Nine newly usable roots include Günter Klein/BWT, Spichala Vermessung, Metta Verlag, Bonner Gerüstbau, Versicherungsagentur Sontag, Böhm Elektrobau, THOMAS Heizung & Sanitär, Immobilien Krudewig, and DGB-Haus Bonn. This removes a homepage fetch barrier; it does not prove those companies have vacancies. The twenty-four-page run successfully fetches **119 distinct hosts**, compared with 95 in the previous six-page run. This includes official sites and linked career sites, not newly verified employer identities.

The [full result artifact](bonn-public-discovery.json) contains all 102 outcomes at each budget, feed URLs and states, source paths, job identities, source employers, rejected external feed evidence, and a manifest of 1,263 hash-validated captures. Of those, 308 were newly observed on 2026-10-09 and 955 were reused from 2026-10-08. Raw bodies and full checkpoints remain local ignored evidence.

## Retained production cases

**Public HTML microdata.** Fraunhofer IAIS links to SuccessFactors job pages containing scoped Schema.org `JobPosting` microdata. Those pages were fetched but produced no postings. The new parser extracts title, description, source employer, nested location, identifier, and date without JavaScript execution. The captured pages yield two concrete Sankt Augustin jobs: a researcher for trustworthy AI and a student assistant for the Big Data/AI alliance. The unusual publisher address string `Sankt Augustin, DE, 53757` and UTC textual date are normalized only when their observed structure is unambiguous. Source employer remains Fraunhofer-Gesellschaft; the institute relationship also has an explicit official page link and IAIS description evidence.

The parser scopes properties to each posting, rejects hidden/initiative/ambiguous records, resolves same-origin relative URLs, preserves distinct identifiers on one listing, and deduplicates the same posting represented in both JSON-LD and microdata. A regression caught the previous generic `Stellenangebote für Studentische Hilfskräfte` heading being treated as a vacancy. It is no longer imported as a job. Existing persisted false positives are not automatically deleted by this change.

**Read-only application-form evidence.** Kampmeyer has real German job-detail pages, but `Immobilienberater` did not pass an English-centric role vocabulary check and no application anchor existed. The page instead publishes an application heading and a CV upload form. That combination now confirms the single title on a job-detail route. Gravity Forms' public upload-container markup is supported alongside normal file inputs. No form is submitted and no upload/applicant endpoint is fetched. Newsletter forms, hidden forms, initiatives, and talent-pool pages do not confirm vacancies. Six pages recover one posting; twelve recover three, including the junior real-estate adviser and sales-assistance roles.

**Partial detail inventory.** A job-detail response cannot establish an employer's full inventory. Microdata and generic detail routes are incomplete even when they contain a valid job or an empty selection. This prevents scheduled refresh of one saved detail URL from closing other jobs merged during discovery. Three isolated SQLite trials use normal discovery persistence and scheduled refresh: Kampmeyer stays at three active rows, Fraunhofer IAIS stays at two, and scoped Canini stays at three; all three saved sources remain incomplete. The trial starts from an empty store, not the historical baseline, and retention does not claim every row was re-observed on the refreshed detail. Source caps and pagination uncertainty remain explicit.

**Native and JSON feed provenance.** ATS recognition is not employer identity. Both recognized ATS feeds and Schema.org JSON feeds now require evidence supplied by a first-party or verified branded career page. The supplier's parent chain is checked before accepting the feed. A direct official link to an unbranded public feed remains valid; following another company's footer to its career feed does not. Rejections are preserved in discovery evidence and the result artifact. This removes 33 Reltio jobs reached through SAP's footer and ten Tucows jobs reached from Ascio; neither feed is added to the seed's saved sources.

**Personio posting scope.** An official link to `/job/<ID>` authorizes that posting, not the tenant's entire XML inventory. The posting route is retained in `board_url`, parsing filters to its ID, and the result stays incomplete. Canini's three explicitly linked postings replace the overbroad 134-row group import. Multiple scoped links to one XML feed merge into one saved source and reuse one fetch; at most 24 scoped IDs are processed per company. Per-posting source markers let refresh update every previously authorized ID while excluding other group jobs. If an official tenant-board link is also present, its full-board refresh authorization survives either link order. SQLite regressions verify shared-feed discovery and counts, both mixed-link orders, and that two scoped postings update while a third unrelated row is not imported. Previously imported group/competitor rows and feeds require an audit/rediscovery; this patch does not delete historical records automatically.

**HTTP policy and pacing.** `HIRING_RESPECT_ROBOTS=false` applies to production homepage verification, career discovery, saved-feed refresh, and detail enrichment. `true` restores robots enforcement. Standalone `Client` and command-line discovery retain their prior robots-checking default; CLI `--ignore-robots` is explicit. The sample runner records that setting in its run metadata.

Discovery and refresh still use the identifying user agent, public-IP validation on network requests, bounded redirects, per-company request caps, and at least one second between requests to each origin. Pacing/cooldown is shared within each process, not across separate replicas. On 429/503, the shared limiter honors numeric/date `Retry-After` or defaults to 60 seconds, returns `origin_backoff` rather than sleeping for the cooldown or retrying immediately, and allows unrelated origins to continue. It rechecks cooldown under the pacing lock so queued workers do not send after a response has paused their origin. A controlled two-thread regression reproduces that case.

The production default is now 12 pages, depth 3, and 48 requests per company; page tuning supports up to 24, and the existing maximum request cap is 64. The experiment used 45 requests per company, a 2,500-live-request shared cap, one-second shared origin pacing, and a 24-page maximum. Replays reused immutable captures; two workers were used for final runs to bound memory. Initial concurrent multi-variant replays exhausted available memory and were discarded, then all 102 outcomes were regenerated sequentially.

## Employer examples and limits

| Employer | Previous 6 pages | Current 12 pages | Current 24 pages | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Telekom | 75 | 75 | 75 | Public adapter still works; national capped selection, only four list Bonn. |
| DHL original `.com` seed | 0 | 0 | 0 | Direct homepage still times out even without fetching robots.txt. |
| BWI | 37 | 71 | 80 | More concrete vacancy URLs from deeper department/navigation pages; incomplete inventory. |
| Kampmeyer | 0 | 3 | 3 | Public CV-form evidence recovers German job-detail titles. |
| Fraunhofer IAIS | 1 generic heading | 2 concrete postings | 2 concrete postings | Actual microdata replaces a listing-heading false positive. |
| Faßbender Tenten | 6 | 10 | 17 | Additional deeper job pages; not a Bonn-only count. |
| TMS | 4 | 6 | 6 | Additional HTML detail pages. |
| Neugart | 3 | 20 | 20 | GC group portal rows still need employer attribution. |
| Ascio | 0 | 0 | 0 | Parent-company Tucows/`remotetcx` feed rejected for missing seed attribution. |
| SAP Leanix | 0 | 0 | 0 | SAP footer leads to Reltio; unrelated Greenhouse feed rejected. |
| Canini Dentallabor | 134 | 3 | 3 | Three explicitly linked Personio IDs retained; other group rows excluded. |

The previous separate DHL German-homepage/Bonn-career trials still establish accessible official alternatives and public Phenom extraction; they are not silently substituted into the fixed `.com` seed here. The original mapped DHL Global Forwarding record still lacks a website. Skipping robots cannot supply missing company identity evidence or repair a timed-out homepage. The previous explicit Telekom Bonn diagnostic remains separate from this nationwide cohort source.

PDF-only vacancies, unsupported JS feeds, public-board pagination, malformed homepage values, and shared-tenant employer attribution remain gaps. The corrected Canini scope removes 131 overbroad rows from fresh results. Remaining shared-host/group-board attribution, especially GC/Neugart, still prevents treating pipeline counts as audited coverage. The sample's 56 unresolved companies include businesses without demonstrated career pages, not just blocked sites. Optional Tavily/Brave credentials were absent; no paid website-search fallback or rendered-browser improvement is claimed.

## Reproduction and verification

```bash
# Requires local hash-checked data/bonn-public/http captures.
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --root data/bonn-public --variant candidate --ignore-robots --pages 6 --workers 2
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --root data/bonn-public --variant public-twelve --ignore-robots --pages 12 --workers 2
.venv/bin/python -m experiments.bonn_discovery_benchmark crawl --root data/bonn-public --variant public --ignore-robots --pages 24 --workers 2
.venv/bin/python -m experiments.bonn_discovery_benchmark summarize-public --root data/bonn-public --output reports/bonn-public-discovery.json
# Append --live to fetch missing public URLs; existing captures remain unchanged.
.venv/bin/python -m unittest discover -s tests -q
```

Behavior regressions were observed failing before fixes. Verification covers robots skipping with redirects and private-IP rejection, Retry-After cooldown across clients and queued concurrent requests, microdata identity/scoping/relative URLs, German CV-form evidence, listing-heading precision, incomplete detail refresh, and multiple scoped links sharing one feed. The full suite passes **525 tests**. All three final replays complete 102/102 companies, all 1,263 capture hashes validate, the three persistence trials retain their expected active rows, and compile/diff checks pass.
