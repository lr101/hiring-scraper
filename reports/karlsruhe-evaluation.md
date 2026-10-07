# Karlsruhe discovery feasibility — 2026-10-05

**Recommendation:** use OSM and regional employer directories to discover companies and their domains, then follow links from each company's homepage to its careers area. Use Arbeitsagentur to add employers currently advertising, and search as a fallback for missing or stale domains. The experiments found **13 accessible career endpoints**, including one placeholder page and one public-sector employer; they do not establish 13 employers with currently open Karlsruhe jobs.

## Test design

- Live unauthenticated HTTP requests from this workspace, Python 3.11 `urllib`, identifiable `HiringDiscoveryResearch/0.1` user agent. No browser or cookies required for successful requests.
- Karlsruhe: 5,000 m radius around 49.0069, 8.4037 for OSM; `wo=Karlsruhe&umkreis=5&angebotsart=1&pav=false` for BA. BA chooses its own location center, so these are approximately comparable geographic scopes, not identical boundaries.
- OSM: named `office`, `craft`, or `industrial` objects. This deliberately excludes many shops, restaurants, hospitals and other employer categories; it is not all Karlsruhe businesses.
- BA: one page of 25 listings and one job-detail request. HTML and API requests were made separately and returned somewhat different first pages. No complete pagination or recall measurement.
- KIT: a complete returned directory page, then extraction of displayed location strings containing the word Karlsruhe. This is a city-address filter, not a 5 km filter; branch offices absent from the displayed address can be missed.
- OSM career sample: first 12 website-bearing `office=it` objects in the saved Overpass response order. This is reproducible but neither random nor representative of all sectors.
- Search: six hand-selected results from three queries, including two BA-employer lookups. No claim of search precision, recall, or direct search-engine scraping access.

## Measured results

| Method | Automated access observed | Yield | Main limitation | Use |
|---|---|---|---|---|
| OSM / Overpass | 200 JSON, 8.22 s, no credential | 1,453 mapped objects; 1,080 website tags (74.3%); 1,012 distinct exact URL hosts | Map objects are not unique legal companies; stale domains, public offices and branch duplicates | Strong initial geographic/domain seed source |
| Arbeitsagentur website | 200 HTML, 0.34 s | Embedded `ng-state` has 25 jobs / 13 employer names | Version-dependent HTML; active hiring only | Working alternative to direct API |
| Arbeitsagentur search API v6 | 200 JSON, 0.17 s, public client header | 25 jobs / 15 employer names; 3 external links; response reports 3,070 total matching jobs | Unofficial integration; external links can be aggregators; recruiting agencies remain | Hiring signal and additional employer names |
| Arbeitsagentur detail v4 | 200 JSON, 0.11 s | One detail response parsed | Selected detail had no employer-career URL; `allianzpartnerUrl` points to BA | Not a guaranteed domain resolver |
| KIT employer directory | 200 HTML, 0.60 s; old URL redirects | 1,445 distinct directory card IDs; 285 Karlsruhe-address cards; 218 of those with homepage links (76.5%) | Nationwide directory with university/recruiting bias; not all cards expose homepage links | Strong complementary regional source |
| CyberForum member directory | 403, “Just a moment...” challenge | No member records extracted | Search index can see a page that this HTTP client cannot | Defer until an authorized feed or reliable access exists |
| Search-assisted discovery | 6 manually curated URLs associated with three recorded queries; all fetched with 200 | 6 manually confirmed career endpoints, including 2 BA-name lookups | Curated sample; search provider API/cost and scale not tested | Promising manual gap-filling step; automated search not evaluated |

Counts can be reproduced in this workspace by `experiments/analyze.py` from the local captured responses. Evidence lives in `data/karlsruhe/*.json`, with per-company requests under `careers/` and `search/`. Full responses are local `.body` files; hashes are tracked. The exact Overpass query is `data/karlsruhe/overpass.ql`.

**Auditability limit:** the Git repository stores derived results and request metadata, not the primary response bodies. The original captures remain available only in this workspace. A fresh clone cannot independently reproduce this dated observation or audit its content/ownership assessments; re-fetching observes a potentially changed web, and hashes cannot recover the missing content. For archival reproducibility, preserve this workspace’s `.body` files alongside the repository.

**Search evidence limit:** the interactive web-search step returned the selected employer pages, and the selected URLs/query strings are recorded in `search_seeds.json`. No raw provider result set or ranking was archived. The reproducible script verifies curated endpoints only; it does not establish search-engine bot access, repeatable discovery yield, or an automated search integration.

**Important BA result:** a request to the API host's `/robots.txt` returned 403, but the actual search and detail endpoints both returned 200 using `X-API-Key: jobboerse-jobsuche`. Do not generalize a host-root/robots failure to the entire API. The website's robots file permits general crawling. The client header and endpoint discovery came from [the bundesAPI project's own documentation](https://github.com/bundesAPI/jobsuche-api); that project is not an official BA API contract. Actual behavior here was verified against BA responses.

BA employer names still include staffing firms such as Page Personnel and persona service despite `pav=false`. Employer identity and agency classification need independent validation. Work location is not company headquarters: a result can display an employer name containing Leipzig while advertising Karlsruhe work.

## Does a company website lead to its own hiring site?

Of the 12 OSM IT entries:

- **9 homepages fetched successfully.**
- **7 yielded career links and a fetched 200 career endpoint.** One (MegaCycle) contains placeholder text, so only **6 were useful career surfaces** on manual inspection.
- **2 had no career link detected on the homepage:** CodeWrights and BAI. This does not prove no hiring site exists.
- **3 were unresolved before homepage retrieval:** PDMLab robots timeout (45 s), Excipio DNS failure, and Technidata's old domain redirecting its robots request to an HTML company page. The conservative probe stopped on these; these are not all bot bans.

TelemaxX initially produced a false positive because an empty `href` labelled “Karriere” resolved to the homepage. A failing regression test reproduced this, the extractor now ignores empty links, and the actual vacancies URL was fetched successfully. Both the original observation and corrected probe are retained (`08_career` and `08_career_corrected`).

The six search-assisted pages all had meaningful career content. Open Experience, PLANTA and Arnotec expose individual vacancy links in HTML. Volksbank pur links from its own site to a Helix-hosted jobs tenant; the ATS itself was not fetched. That employer-to-ATS link is stronger ownership evidence than a search hit on an unrelated jobs domain.

See [the checked endpoint table](../data/karlsruhe/verified_career_sites.csv) for all 13 endpoints, provenance, status and caveats. Examples:

| Employer | Confirmed career endpoint | What was established |
|---|---|---|
| ISCL | https://iscl.de/karriere/ | Career page and job titles in HTML; local OSM office |
| TelemaxX | https://www.telemaxx.de/ueber-uns/karriere/berufserfahrene | Actual vacancy page, not just a menu label |
| Ratiodata | https://karriere.ratiodata.de | Company homepage links to career subdomain |
| Unic | https://www.unic.com/de/jobs | Company career area with role links |
| Volksbank pur | https://www.volksbank-pur.de/meine-bank/karriere.html | Karlsruhe BA employer + company career page + explicit ATS link |
| Open Experience | https://openexperience.de/de/karriere/ | Own-domain vacancy links; Karlsruhe and Baden-Baden distinguished |
| PLANTA | https://www.planta.de/karriere/ | Own-domain vacancy links; multiple company locations |
| Arnotec | https://job.arnotec.de/ | Employer-branded subdomain with Karlsruhe vacancy links |

“Own hiring website” should include a company-domain page, a career subdomain, or an employer-authorized hosted ATS reached from the company website. An aggregator repost does not qualify on its own. Company presence in Karlsruhe, a careers website, and an actual Karlsruhe vacancy should be three separate fields.

## Access and operating constraints

No observed successful source required browser rendering, but a landing page being readable does not show that its complete jobs feed is readable. No browser automation test was performed for the blocked site; the 403 is specific to this client/network/date. We did not bypass the challenge.

Robots were inspected for discovery sources and checked before company probes. Unknown/unavailable company robots responses were conservatively skipped. These scripts are research tools: their generic HTTP fetcher automatically follows redirects; per-hop robots revalidation, complete robots-standard support, and persistent rate limits remain production work. Robots availability and HTTP success do not decide contractual reuse permission.

Overpass's robots excludes general indexing of `/api/`; its [documented API](https://dev.overpass-api.de/command_line.html) explicitly supports programmatic queries. We made one bounded query as an API client. For recurring large-scale discovery use cached regional extracts or a suitable hosted/self-hosted service: the [operator's policy](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html) warns against using the public instances as a sustained application backend. OSM data requires [OpenStreetMap attribution and ODbL consideration](https://www.openstreetmap.org/copyright).

The [KIT directory](https://www.careerservice.kit.edu/de/studierende/berufsorientierung/unternehmensuebersicht/) is accessible in HTML; its robots excludes `/ajaxfiles/`, which we did not call. CyberForum's robots and its actual challenge response are saved separately; robots allowing a path does not guarantee access.

## Proposed next implementation

1. Persist companies separately from locations and source observations. Keep name, canonical domain, location evidence, source URL, discovery timestamp and confidence. Normalize domains carefully; multiple branches and multiple companies can share a domain.
2. Import OSM and regional-directory seeds, add BA employer names, then resolve websites and validate careers links. Use a confidence queue for stale domains, redirects, public employers and staffing agencies.
3. Store verified career endpoints with ownership evidence and extraction type: HTML, structured `JobPosting`, or known ATS. Separate “no jobs” from failed fetching, blocked responses and parser failures.
4. Only then add periodic job checks. Prefer stable provider job IDs, otherwise canonical URLs; track first/last seen and content hashes. Mark disappeared jobs closed only after successful complete fetches, and keep baseline imports separate from genuinely new postings.
5. Run a larger stratified sample across sectors before estimating coverage or choosing a crawl budget. This study measures feasibility and a small sample's conversion, not Germany-wide recall or long-term uptime.

The repository contains executable experiments and saved outcomes. Scheduling, a job database, notifications, full ATS extraction, and production hardening are intentionally future work.
