# POC review and next accuracy experiments

**Review date:** 2026-10-07. **Scope:** company discovery, vacancy extraction, and profile matching. Recommendations below are proposed experiments; their targets are acceptance criteria, not measured gains.

The strongest next opportunities are to improve employer/source identity, finish incomplete sources, enrich sparse HTML jobs, and separate candidate retrieval from match ranking. A wholesale scraper replacement or another round of rule-weight tuning has less support in the evidence. The system has demonstrated extraction gains, but company precision and market-wide job recall remain unmeasured.

This review reconciles the committed run-31 results with its summary and aggregates them without network access. [Derived measurements](poc-review-analysis.json) include input SHA-256 hashes. [The analysis script](../experiments/poc_review_analysis.py) requires only Python's standard library:

```bash
python3 -m experiments.poc_review_analysis --output reports/poc-review-analysis.json
```

The external documentation was checked during this review. No new browser, paid API, model, production import, or nationwide crawl was run. Full HTTP bodies and the later CV study's job-input files are absent from this checkout, so extraction replay and a fresh semantic ranking comparison need those inputs reacquired or restored. The measurements here aggregate derived evidence; they do not rerun the old extraction experiments.

## What the POCs actually establish

| Study | Observation | Interpretation |
| --- | --- | --- |
| [Full crawl and parser follow-up](full-crawl-domain-and-html-followup.md), run-31 | 2,251 OSM candidates; 1,606 website-bearing records; 669 positive page/feed/provider outcomes; 1,632 accepted source rows | 41.7% discovery yield among website seeds. Neither 2,251 legal employers nor 1,632 unique current/local vacancies is established. |
| Employer attribution filter, run-31 | 293 of 834 HTML rows excluded from 55 unverified external pages | A useful attribution safeguard. Excluded rows have insufficient evidence; they are not all individually proven false. Relaxing the filter to inflate counts would invalidate the gain. |
| [Homepage POC](homepage-discovery-poc.md) | Six identity-confirmed email-domain additions over 13 automatic domain resolutions | A concrete small increment. Six successful checks do not establish population-level precision. The six additions remain in a preview, not the production fixture. Including the earlier manual match, the fixture has 14 resolved missing-website records; the preview would have 20. Thus 631 remain unresolved in that fixture, or 625 after applying the preview. |
| [JavaScript extraction POC](javascript-career-extraction-poc.md) | Generic framework state yielded zero jobs in a 250-capture slice; explicit Wibu cards supplied nine stable identities, seven previously unseen titles | Keep explicit card contracts. Framework/API-looking strings alone are weak reasons to invest in arbitrary JavaScript parsing. The nine identities are not nine net extra rows over two prior synthetic hints. |
| [Detail hydration POC](profile-matching-poc.md) | Scoped HTML plus JSON-LD improved 7/20 descriptions, compared with 2/20 for JSON-LD alone | Useful evidence for focused enrichment. The old 1,664-row fixture gained seven descriptions, only 0.42 percentage points of overall coverage; 35% is the selected attempt success rate. |
| [CV board evaluation](../docs/cv-profile-board-findings.md) | On the final 120 records, rules found 8/20 relevant jobs; 38 returned, 30 judged unlikely; nDCG@10 0.5101 versus original 0.3028 | Better top ranking with incomplete recall and noisy broad inclusion. The selected rules' precision was 21.1%, versus original 62.5% on only eight returns. Labels are model-assisted, not independent human judgments. |
| Lexical algorithms and fusion in the CV study | Alias/title BM25 found 20/20 pooled relevant records but returned 113/120; nDCG@10 0.1820. Fusion variants ranked worse than rules | Broad lexical retrieval contains useful candidates, but is a poor final ranker here. The fusion implementation only reorders current-rule candidates, so it cannot recover the 12 relevant records omitted by those rules. |
| [Page-budget experiment](career-discovery-improvements.md) | Increasing six to twelve pages added capture traffic without changing the 25-seed outcome counts | No support for doubling the global limit. Test targeted escalation with a fixed total request budget. |

The CV study locked rules-v11 before final evaluation. Its rules-v12 correction rechecked the same final inputs with unchanged aggregate metrics; that is a post-test check. Future work must use a new untouched holdout. Validation precision was 59.2%, falling to 21.1% on the final pool; relevant prevalence also fell from 47/149 to 20/120. This warrants broader testing rather than attributing the whole change to overfitting.

## Additional opportunities visible in the saved evidence

The new offline aggregation reveals four useful concentrations of work:

- **Repeated observations:** 1,632 rows contain 1,424 exact distinct posting URLs, a difference of **208 rows (12.7%)**. Provider/tenant/posting-ID grouping independently gives 1,424 identities. There are 202 parsed feed observations but 173 exact provider/tenant/feed-URL combinations. These are opportunities to share fetching and group observations, not permission to merge legal employers. Current board code already deduplicates URLs and known requisitions; therefore this is not a claim that 208 duplicates still appear in the UI.
- **Sparse descriptions:** 307 rows lack descriptions and 163 have fewer than 350 characters: **470/1,632 (28.8%)**. HTML contributes **431/470 (91.7%)**. HTML has 204 heading-based rows (111 role headings and 93 open-position headings), which deserve particular scrutiny for evergreen programmes and synthetic identities. Length is a triage signal, not proof of useful requirements.
- **Incomplete feeds:** 29 parsed feed observations represent 25 exact incomplete sources. Two observations of the same Engel & Völkers Lever tenant each contain exactly 100 rows. The adapter requests `limit=100` and marks such responses incomplete; the worker does not currently aggregate additional pages. Missing jobs beyond that page are an unquantified recall gap.
- **Resolvable versus inaccessible failures:** Of 937 unresolved company records, 574 had a successful initial page, 283 stopped on unavailable robots policy, 52 on HTTP error, 26 on explicit robots denial, and two on response size. Of all unresolved records, 259 reached the six-page cap. These are different intervention cohorts; browser rendering is not a remedy for every failure.

Unsupported-provider observations also overstate integration opportunity if counted as distinct boards:

| Provider | Unsupported URL observations | Raw distinct tenant labels | What to inspect next |
| --- | ---: | ---: | --- |
| Softgarden | 33 | 14 | Branded lists and linked DataFeeds first; `jhfiles` and `jobdb` are shared infrastructure labels requiring separate handling. |
| Workday | 14 | 8 | Public career-list/detail rendering, pagination, and tenant/site identity; some observations are login routes. |
| SmartRecruiters | 12 | 7 | Four plausible employer labels: Coface, DreesSommerSE, Tipico, synava. `cdn-cgi`, `external-referrals`, and `oneclick-ui` are suspect infrastructure/utility routes. |
| Recruitee | 9 | 3 | cluetecgmbh, nemenergy, virtual7jobs; distinguish actual jobs from initiative applications and locale variants. |
| Onlyfy | 6 | 2 | Public job/detail routes; policy and application URLs must not count as boards. |
| SuccessFactors | 2 | 2 | Inspect public lists; login-flow observations do not establish readable feeds. |

These are unverified leads. The five companies whose final status is `ats_identified` are not the entire adapter opportunity: unsupported boards can coexist with a parsed HTML source or another company outcome. Separately, Personio has 12 failed feed observations; a failed XML endpoint is not an empty board.

## Company accuracy and discovery

### 1. Introduce employer identity above source establishments

Retain each OSM/BA/directory observation and its location. Add a canonical employer identity with explicit relationships such as branch, group member, shared board, and staffing agency. Store a board independently of its company observations so the same endpoint can be fetched once and attributed using posting evidence.

Start with exact source IDs, canonical vacancy URLs, verified domains, legal identifiers when available, and official cross-links. For ambiguous links, generate candidate pairs using name similarity, postcode, address, phone, and domain; classify or review the pair using independent evidence. A shared domain, nearby point, or one shared name token is insufficient for an automatic legal-entity merge. Review the current permissive name-token checks in `discovery._employer_matches` and `osm_websites._identity_score` against hard negatives such as group subsidiaries and similarly named businesses.

[Splink's probabilistic linkage model](https://github.com/moj-analytical-services/splink/blob/master/docs/topic_guides/theory/fellegi_sunter.md) is an option once multiple sizeable datasets and labeled pairs justify it. For this corpus, exact links plus a small interpretable pair scorer are simpler. Measure pair precision/recall, erroneous group merges, employer-attribution precision, and repeated endpoint requests. A fall in company count can represent improved accuracy.

### 2. Use search and directories to resolve difficult leads and discover new employers

The email preview is the cheapest proven supplement. Its one likely typo rejection suggests testing character n-grams/edit distance for candidate generation while retaining public-page identity confirmation. Apply the six reviewed additions in an isolated import and measure careers/local jobs separately; a verified homepage is not a hiring result.

Next, compare OSM-only discovery against OSM plus KIT/regional directories, BA hiring signals, and bounded search. Use a documented API such as [Brave Web Search](https://api-dashboard.search.brave.com/app/documentation/web-search/responses) for reproducible queries: employer name + town/address + official site, followed by employer + jobs/karriere. Save query, result rank, timestamp, and the official-page evidence. Search snippets nominate leads; verify imprint/contact/location before promotion. BA can discover employers absent from OSM, but work location alone does not establish an office and agency clients often remain anonymous.

Separate **missing-domain resolution for known records** from **new-employer discovery**. The saved small BA/TED exact-name join found no missing-domain additions, which does not show those sources lack new employers. Wikidata's unmatched domains need type/address review; municipal headquarters centroids cannot verify local establishment coordinates.

For larger regions, [Geofabrik's OSM extracts](https://download.geofabrik.de/europe/germany.html) can replace repetitive public Overpass acquisition. This improves reliability and repeatability; it does not independently correct OSM's coverage gaps.

## Job accuracy, coverage, and scraper integrations

### 3. Complete supported feeds before adding more vendors

Implement bounded Lever pagination using documented `skip`/`limit`, collecting unique IDs until exhaustion or budget. Keep the entire scan incomplete if any page fails, repeats unexpectedly, or hits the cap; never close jobs from a partial result. [Lever's official API documentation](https://github.com/lever/postings-api) specifies pagination and public published postings. Compare full-board unique identities and in-area jobs against the current first-page baseline. No numerical recovery is claimed until additional pages are captured.

For capped BA searches, evaluate partitioning broad role queries by occupation, smaller geographic tiles, and available filters, then deduplicate posting IDs. Partitioned searches can still overlap or cap; retain reported totals and truncation flags. The CV capture's Weiterbildung search stopped at 300 of 1,541 reported hits. A reported hit total is not a labeled vacancy denominator.

### 4. Add adapters according to validated opportunity and access contract

- **Recruitee:** pilot its documented XML job feeds on the three observed tenants. [The vendor FAQ](https://support.recruitee.com/en/articles/8213076-faq-api) lists XML and JSON routes. [Current authentication documentation](https://docs.recruitee.com/reference/authentication-1) announces Careers Site API tokens by **10 February 2027**, while stating XML feeds and the widget are outside that token scope. Prefer the documented feed route, verify live availability/schema/completeness, and avoid relying on permanently anonymous JSON access.
- **Softgarden:** prioritize employer-linked `jobs.feed.json`, then static branded lists/detail pages. Existing Schema.org support already parses some of these feeds. The [documented Jobboard API](https://dev.softgarden.de/career-websites-api/jobs-api/jobboard-api/executing-a-job-search/) requires a bearer token; it is not evidence for a universal anonymous endpoint.
- **SmartRecruiters:** investigate its [documented posting endpoints](https://developers.smartrecruiters.com/docs/endpoints), including pagination and detail hydration, on verified company boards. [The current overview](https://developers.smartrecruiters.com/docs/posting-api) discusses API-key/OAuth access. Confirm the contract for the chosen endpoint/tenant rather than assuming all documented APIs are keyless. Use public HTML when no suitable feed is available. Remove utility-route detections from the opportunity count.
- **Workday/custom portals:** test public rendered pages before inventing provider-wide API support. A browser-observed internal endpoint is a site-specific experimental contract until its access and schema are established. Keep authentication and login routes outside job discovery.

For each adapter, measure additional **unique active in-area vacancies**, employer-attribution precision, field accuracy, total requests, and failures across multiple tenants. National board rows and duplicated language variants do not establish local coverage improvement.

### 5. Enrich sparse HTML and PDF evidence selectively

Prioritize unique detail URLs with trusted employer provenance, plausible role fit, and missing requirement evidence. The current enrichment pass selects by job ID; test a fair queue that balances employers/providers and prioritizes expected useful information. Normalize benign title suffixes such as location only when canonical URL/provider identity and page context also agree. Do not broaden identity acceptance to similar-but-different role qualifiers.

Evaluate [Trafilatura](https://trafilatura.readthedocs.io/en/stable/extraction-overview.html) as a text-extraction helper **inside a verified vacancy container**. Whole-page boilerplate removal can still retain benefits, staff biographies, and other jobs; preserve headings/bullets for requirement scope. For employer-linked vacancy PDFs, test [pypdf](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) with byte/page/time limits, text-layer extraction first, and OCR only for scans. Recover employer, title, location, deadline, and requirement spans from the document; an initiative PDF remains a lead rather than an active vacancy.

Run a paired 100-detail pilot, stratified across sparse HTML, structured feeds, and PDF leads. Compare the same postings before/after using field-level labels and matching labels. Measure valid description recovery, mandatory/optional extraction F1, and false eligibility changes. More characters alone are not success.

### 6. Use a bounded browser fallback, evaluated against the existing crawler

| Tool | Fit here | Decision |
| --- | --- | --- |
| Existing `Client` + parsers | Already captures responses, validates redirects/public addresses, paces requests, and preserves provenance | Keep as baseline and primary transport. |
| [Playwright](https://playwright.dev/python/docs/network) | Render verified public job lists; inspect XHR/fetch; capture DOM/HAR for repeatable tests | First browser fallback to benchmark. Every relevant network request needs the same origin/access restrictions and budget; checking only the initial URL is insufficient. |
| [Crawlee for Python](https://crawlee.dev/python/docs/quick-start) | Browser/HTTP crawler framework with queues | Consider if managing many rendered sources becomes the bottleneck; introduces a second scheduler alongside existing workers. |
| [Crawl4AI](https://docs.crawl4ai.com/) | Browser-backed extraction with CSS/XPath and optional model strategies | Compare deterministic extraction on the same cohort; avoid generic Markdown flattening of requirements. |
| [Scrapy](https://docs.scrapy.org/en/latest/topics/autothrottle.html) | Mature crawling with adaptive throttling | A scale/operations option, without evidence of extraction-accuracy gains from replacement alone. |
| [Firecrawl](https://docs.firecrawl.dev/features/scrape) / [Apify Actors](https://docs.apify.com/actors/development) | Hosted scraping/extraction or a packaged adapter | Optional comparison arm; require raw evidence, reproducible versioning, attribution, and measured cost per valid extra job. Vendor output still passes the same validator. |
| [JobSpy](https://github.com/speedyapply/JobSpy) | Cross-board lead discovery | Secondary comparator, not canonical employer truth. Its README describes result caps and source-specific rate limits; require verified links and deduplication. |

Choose 30 verified career pages with evidence of missing rendered jobs; exclude access-denied pages and also keep a random accessible-page sample to estimate broader utility. Compare static extraction with deterministic browser DOM extraction, and optionally one framework/hosted arm, using the same page identities and contemporaneous human reference lists. Record valid extra vacancies, false rows, local-role recovery, browser seconds, RAM, requests, and cost. Browser fallback should trigger on evidence of missing content, not just a Next/Nuxt marker. Preserve denied/unknown outcomes rather than counting them as empty boards.

## Matching algorithms worth testing next

### 7. Retrieve broadly, then rank on role and requirement evidence

The strongest untested algorithmic change is a separate candidate-generation stage:

1. Apply verified expiry, geography/country, and explicit user constraints; preserve unknowns for review.
2. Generate candidates as the union of existing-rule candidates, fielded BM25 with reviewed aliases, and multilingual dense retrieval. Do **not** require score >=20 from the current rules before entering the union.
3. Rerank candidates using role duties, occupational context, and applicant requirements. Keep benefits/navigation out of the representation and preserve primary versus adjacent role preferences.
4. Apply evidence-backed conflicts and display unknown qualification gaps. Similarity cannot satisfy a license, CEFR requirement, student enrollment, or specialist tenure.

[Sentence Transformers describes retrieve/rerank](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html). Benchmark [BGE-M3](https://huggingface.co/BAAI/bge-m3) multilingual embeddings and [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) as candidates, not assumed winners. General retrieval-model relevance is not applicant suitability. Encode reviewed profile evidence rather than an undifferentiated CV, and chunk requirements/duties so long descriptions do not lose specialist restrictions through truncation. At roughly 1,000 scoped jobs, an offline exact vector comparison is adequate for a POC; a new vector database is unnecessary.

Use ablations: rules alone; fielded lexical union + rules ranking; dense union + rules ranking; union + cross-encoder; then evidence-feature ranking. This separates gains in recall from gains in ordering. Retrieval Recall@100 and final precision/nDCG must both be reported. The old fusion result does not refute broader retrieval, because its candidate set was fixed.

### 8. Expand occupation concepts and calibrate evidence, not just scores

Pin a local German/English [ESCO release](https://esco.ec.europa.eu/en/use-esco/download) and version concept IDs, preferred labels, and reviewed aliases. Test occupational context for project/product coordination, customer operations, and learning/development, where generic titles conceal technical specialisms. Do not expand every related occupation into an equivalent role or treat ESCO's occupation-skill links as requirements stated by this employer.

Add explicit source spans and section/scope/confidence to requirements. Optional structured-model extraction can propose mandatory versus optional qualifications, but each accepted field needs an exact source span and consistent interpretation; a model's unsupported inference cannot create a hard exclusion. Measure language/degree/license/experience extraction separately, including alternatives and nontechnical roles.

Once multiple profiles have human labels, compare a regularized linear evidence scorer before a more complex [LightGBM ranking objective](https://lightgbm.readthedocs.io/en/stable/Parameters.html). Useful features include occupational fit, required versus optional skills, specialist evidence, academic versus professional tenure, unknowns, and evidence quality. The current 580 model judgments across one CV do not establish cross-profile generalization. Reserve an independent calibration split if testing [probability calibration](https://scikit-learn.org/stable/modules/calibration.html); neither a heuristic 80/100 nor a reranker's sigmoid output is an 80% hiring probability.

## Experiments and measurable adoption gates

These are proposed pilot gates. Lock them before inspecting fresh holdout labels; meet them on the stated cohort before rollout. Sample confidence intervals can still be wide.

| Order | Experiment and cohort | Primary gate | Guardrail |
| --- | --- | --- | --- |
| 1 | Employer/board identity audit; label 300 candidate links and include group, agency, similar-name, and branch cases | >=98% observed accepted-link precision; publish recall, false-merge cases, and confidence interval | No legal-entity merge based only on domain/title/proximity; retain source observations. |
| 2 | Apply six reviewed homepage leads in an isolated fixture; then 100 missing-domain records plus 100 new source leads | >=15 additional verified homepages in the 100-record resolution pilot, with >=98% observed attribution precision | Report new verified local employers separately from resolved existing records and hiring outcomes. |
| 3 | Lever full pagination and three Recruitee tenant pilots, then validated Softgarden/SmartRecruiters boards | Demonstrate complete bounded enumeration on available reference boards; expansion pilot adds >=20 unique active in-area jobs over baseline | >=98% audited vacancy/employer precision; incomplete scans never close jobs. An empty valid board can pass correctness without meeting a coverage target. |
| 4 | 100 sparse job-detail/PDF reads; fair prioritized queue versus equal-budget current queue | >=25 verified description recoveries, plus a positive change in labeled requirement coverage | Wrong-posting hydration <=1%; precision of extracted mandatory requirements >=95%; review induced match changes. |
| 5 | Static versus browser on 30 verified missing-content pages | >=20% more audited unique vacancies in that deliberately difficult cohort | >=98% observed extraction precision; publish local-job gain and cost, not general-population extrapolation. |
| 6 | Fresh retrieval/reranking study across 6–10 profiles, 150–200 judged pairs/profile | Candidate Recall@100 >=85%; final mean nDCG@10 improves >=0.10 absolute and P@10 >=70% | Report per-profile results, paired confidence intervals, and critical-conflict leakage; no material decline hidden by averages. |
| 7 | Held-out source/geography/freshness audits over repeated scans | Improve correct in-area unique vacancy count and stale-job rate versus baseline | Preserve remote-country unknowns, failed-fetch state, and incomplete-search lifecycle protection. |

For experiment 6, run rules-v12 on the **new** labels as the contemporaneous baseline. The old 0.5101 nDCG@10 and 50% P@10 are context, not transferable baselines for a different pool. If two methods improve different dimensions, compare precision at fixed recall and recall at fixed precision; changing a threshold alone is not an overall accuracy gain.

Budget live tests by requests and elapsed/browser time; model tests by inference time and tokens/cost where relevant. Compare arms under equal budgets. Reserve a random portion of the crawl/enrichment queue so prioritization does not systematically neglect new or nontechnical employers. Report incremental verified jobs per 100 requests and per unit cost, with attribution and geography accuracy beside yield.

## Evaluation that can support an accuracy claim

Company truth must distinguish a real organization, legal employer, local establishment, verified domain, and authorized career board. Job truth must distinguish an active vacancy, programme/initiative lead, unique requisition, employer attribution, expiry, work locations, and remote applicant scope. A first-party page can still contain a stale or generic programme heading.

Build contemporaneous human-reviewed reference lists for a stratified employer cohort across at least three regions, multiple industries, large/small employers, and provider/HTML/PDF sources. Audit positive outputs **and** unresolved/rejected records. Compare the union of independently checked sources and manual official-page enumeration; report reference-cohort recall rather than claiming complete market recall. New sources need source-marginal unique employer/job gain, since duplicate volume is not coverage.

For matching, label role relevance and eligibility evidence separately, with reason codes for language, occupation, qualifications, tenure, geography, and insufficient evidence. Double-review a subset and adjudicate disagreements. Pool candidates from all algorithms plus random scoped negatives, including records below current thresholds; label the scored output set sufficiently to calculate precision. If stratified sampling is used for population estimates, apply sampling weights and disclose unjudged records.

Keep duplicates and language variants in one split; separate tuning/calibration from a locked final test, and include held-out profiles, employers, and later capture dates. Bootstrap matching comparisons by profile and account for employer/vacancy clustering; do not treat repeated establishments as independent evidence. Publish sample sizes, relevant prevalence, confidence intervals, source/model hashes, and budget/completeness flags.

Integrate through the existing seams: `osm_websites.py`/`location_discovery.py` for company leads; `ats.py`, `http.py`, and `app/worker.py` for validated adapters and pagination; `html_jobs.py`/`app/enrichment.py` for detail evidence; `app/board_scope.py` for geography/expiry/identity; and `matching.py`/`experiments/cv_profile_evaluation.py` for candidate/ranking comparisons. Keep research adapters optional until their fresh cohort passes. Regional and supplemental CV fixture sources currently remain unscheduled, so a measured refresh path is necessary before claiming ongoing coverage.

The recommended first implementation sequence is **identity and completeness → targeted evidence enrichment → broad retrieval with evidence-based reranking**. It addresses measurable defects already present in the artifacts and provides a fair route to deciding whether additional scraper tools or models earn their cost.
