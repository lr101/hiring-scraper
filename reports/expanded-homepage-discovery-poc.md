# Expanded homepage discovery benchmark — 2026-10-08

The corpus increased from **14 to 120 missing-homepage records**. The matching policy before production promotion plus mapped Wikidata websites recovered **15/120 (12.5%)**. The tested approaches produced **24 audited company homepages (20.0%)**, an additional **9**, or **60% more recoveries**. Missing homepages fell from **105 to 96 (8.6% fewer)**. The experimental heuristics produced unsafe matches and have not been promoted wholesale into the production resolver. The subsequent guarded implementation is documented in [production homepage changes](production-homepage-implementation.md).

The nine additions were all in the new sample: **11 → 20 of 106** sampled records (10.4% → 18.9%). The original diagnostic cohort stayed at **4/14** after rejecting a related-entity proposal. These figures describe this corpus, not expected recovery across every missing record.

## Corpus and definition

- Saved source snapshot: `fixtures/karlsruhe-osm-candidates.csv`, 2,251 records, 645 without a website.
- Preserve the original 14 diagnostic cases. Select another 106 from the remaining 631 missing records using the smallest SHA256 values of `homepage-expanded-v1:<osm_type>/<osm_id>`; selection is reproducible and independent of search outcomes.
- Refresh all 120 records' tags through five public OSM multiget requests. No refreshed record had a website tag. **40/120 lack both source city and postcode; 16 have an email address.** The cohort also includes public bodies, insurance branches, universities, trades and individual professionals.
- A recovery means an audited organization-owned homepage association. Official brand/parent sites are included when the source explicitly links to that organization or the organization publishes the relevant branch/center. A directory profile is excluded. This is not a promise that an OSM office remains at exactly its saved address.
- The baseline uses **production matching policy with research budgets**. To compare every eligible record, it allows one search per eligible company and more crawl requests than a normal bounded production batch. It does not measure the yield of a single production run with its default 50-search cap.

## Measured results

The first table is a **sequential residual pipeline**, not independent success rates on identical populations. Each method receives records left unresolved by earlier experimental stages. The pipeline retains raw proposals for audit; two unsafe early proposals were excluded from the final union. Later stages consequently did not retry those two records. Confirmed totals are a measured lower bound under this execution order.

| Approach | Residual records considered | Raw proposals | Audited additions | Cumulative confirmed |
|---|---:|---:|---:|---:|
| Existing OSM → Wikidata P856 links | 120 | 5 | 5 | 5 |
| Current email + basic Tavily matching policy | 115 | 10 | 10 | 15 |
| Reuse existing mapped websites by exact normalized name | 105 | 0 | 0 | 15 |
| Existing search hits: more same-host pages + compact/German brand spelling | 105 | 3 | **1** | 16 |
| Remove legal forms from query; ask for contact/imprint; add regional search hint where city is absent | 102 | 6 | **6** | 22 |
| Follow explicitly labelled website links from known third-party profiles | 96 | 0 | **0** | 22 |
| Alternate official-website query with advanced search | 25 selected; **22 queries available** | 2 | **2** | 24 |

The final advanced residual subset changed after more rewrite matches were captured. Three newly selected queries were not in the cache and were deliberately not bought during the completion replay. **Do not interpret this row as a complete 25-company advanced-search success rate.** The paired control below completes that comparison on a fixed 25-company subset.

The current-policy baseline's ten direct verifications were **three source-email domains and seven search results**. Its 72 basic queries used 69 provider-reported credits. The rewritten query stage issued 102 queries and used 95 reported credits; seven responses reported zero usage. These are recorded usage figures, not assumed list-price charges.

The directory approach produced outbound leads for **18 of 96** records but none passed verification. Some were another registry/profile, a generic parent homepage, an inaccessible site, or lacked enough company identity evidence. A zero verified yield does not establish that those companies have no website.

Exact-name reuse had no useful unresolved peer match. The only exact-name website peer in the entire 120-case corpus was Allianz, already recovered through Wikidata.

## Paired search-depth control

Use the fixed 25-company residual subset from the first run. For each company, send **the same query and country/result limits** once at basic depth and once at advanced depth. Verify the first three eligible results with the same page verifier. All 50 searches and verifications have captures/results; the advanced searches reuse the original acquisition.

| Tavily depth | Companies | Audited homepages | Provider-reported acquisition credits |
|---|---:|---:|---:|
| Basic | 25 | **4** | **25** |
| Advanced | 25 | **4** | **50** |

Both depths resolved the **same four companies and domains**: Wolfgang Schmitt, Holzbau Stengel, the Steinbeis center, and Gastronomie-Service Glaser. Two had already been found by the rewritten-query pipeline. **Advanced depth added zero unique verified homepages over basic in this control.** This supports trying another basic query before paying for advanced search; it does not prove advanced is never useful.

## Structured-data control: what failed

An independent offline hypothesis used the existing `Document` JSON-LD parser. Require an exact company name, exact city/postcode/street/house number, an Organization/LocalBusiness type, and a schema `url` on the page's host. There were **79 address-eligible records**, **1,378 inspected page/record pairs**, and **782 pairs containing JSON-LD**. Shared search-result hosts can appear against multiple records; these are not distinct-page counts.

The hypothesis proposed **7 homepages; all 7 were directory/registry profiles; 0 were confirmed owned homepages**. Hosts included Houzz, minanner, Lokalatlas, handelsregister.ai, fallswaspassiert and itsbetter. Their schema accurately describes a listed company while `url` identifies the listing. This approach needs separate publisher/operator evidence or an outbound owned-site URL before it can approve a homepage.

## Audited additions and exclusions

| Company | Confirmed homepage | Evidence |
|---|---|---|
| FALC Immobilien | falcimmo.de | Official branch page, exact Pfinztalstraße 59 / 76227 address |
| Rechnungshof | rechnungshof.baden-wuerttemberg.de | Official organization contact page; source building number 50 differs from current 80 |
| BKV-Logistik GmbH & Co. KG | bkv-logistik.de | Exact legal entity and address in imprint |
| Wolfgang Schmitt Elektrotechnik | wschmitt-elektro.de | Own company/contact page; source house suffix 18a differs from published 18 |
| STUMPF OHG Allianz | stumpf-ohg.de | Imprint operator and exact source address; Allianz affiliation on owned site |
| Urich Transporte | urich-transporte.de | Imprint company name and exact address |
| Zimmerei und Holzbau Stengel GmbH | holzbaustengel.de | Exact legal name and address on contact page |
| Steinbeis-Beratungszentrum Unternehmensentwicklung und KI | steinbeis.de | Official center profile, exact name/address/phone; organization homepage |
| Gastronomie-Service Thomas Glaser | gastronomie-service-glaser.de | Own contact page, exact source phone 0721/9200815 |

The broader verifier's other two proposals are excluded:

- **Stober Metallbau → metallbauer.io: false match.** The industry word `metallbau` matched a directory's domain. Its legal operator is unrelated to Stober.
- **Vi2vi GmbH → vi2vi-systems.com: review needed.** Historical mentions of vi2vi GmbH and a Durmersheim footer combine with current legal operator vi2vi Retail Solution GmbH in Malsch. The source identifies another entity/address. Combining page text can manufacture convincing but inconsistent identity evidence.

The raw pipeline therefore produced **26 proposals: 24 confirmed, 1 false match, 1 unresolved related-entity case**. This is an audit of proposed positives by one reviewer. There is no complete ground truth for the other 96 records, so recall and population precision are unknown. The seven independent JSON-LD proposals were also audited and rejected.

Joas Immobilien appeared in the initial exploration but is excluded from the final pipeline result: consistently verifying the first three eligible results preserves competing-domain ambiguity. Choosing whichever domain appears first would inflate the gain.

## Costs, bounds and reproducibility

- **231 newly spent provider-reported Tavily credits**, plus **8 previously spent credits reused**, for **239 total acquisition credits**. The 224 cached search requests have a nominal basic=1/advanced=2 cost of **249 credits**; ten basic responses reported zero credits. API responses are the source of measured usage.
- **2,254 live website request attempts**, including robots, redirects and failed access attempts: 1,800 in the initial capped run, 447 in the completion crawl, and 7 in the paired control. Shared cached captures reduce later work. These are aggregate study costs, not isolated per-method marginal crawl costs.
- Five public OSM requests; no paid data vendor besides Tavily. Existing Wikidata cache and all-corpus CSV reused.
- Initial crawl hit the 1,800-request cap during rewrite. Its subsequent zero yields are **censored** and excluded from effectiveness claims. The saved search responses were replayed with another 1,400-request allowance; only 447 additional attempts were needed. The initial exploratory query stage evaluated up to five eligible hits before slicing; the final replay consistently evaluates the first three. Initial artifacts are retained separately and are not final metrics.
- Robots and public-destination checks remain active. No proxy/crawler was used to circumvent denied sites. Short timeouts, cached failures, static HTML and stale source tags can all leave genuine homepages unresolved.
- Browser automation was attempted, but the workspace reports **no preview automation host available**. Browser rendering has no measured yield here and is not reported as a zero-success approach. Tavily Extract was not benchmarked.

Artifacts:

- `data/website-discovery/expanded/cohort.json` and `corpus-metadata.json`: source records, live tags, selection and coverage.
- `search-cache.json`: public query/URL/title leads, reported credits and prior-run attribution; no credentials.
- `initial-capped-results.json`, `crawl-completion-results.json`: live cost provenance.
- `completed-results.json`: final raw offline replay, completion marker, residual denominators and unavailable-query counts.
- `paired-depth-results.json`, `structured-owner-results.json`: controls.
- `manual-audit.json`: individual decisions, public evidence links and available capture hashes.
- `audited-summary.json`: audited union and all final reported numbers.
- HTML bodies and HTTP metadata remain in the ignored local `http/` directory.

Reproduce the final aggregate without network or credentials:

```sh
.venv/bin/python -m experiments.summarize_expanded_homepages
```

This is a historical benchmark of the pre-promotion matching policy. The experimental pipeline imports production functions, which have since changed; rerunning it will not reproduce that old policy. Preserve the archived raw results for historical aggregation. Replay the new production policy into a separate artifact, without network or credentials:

```sh
.venv/bin/python -m experiments.production_homepage_replay
```

The checked-in search/audit/summary artifacts are enough to reproduce aggregation. Full page verification additionally requires the ignored captures; another checkout must recapture public pages. Live runs require private `TAVILY_API_KEY` configuration and may produce changed results. Keep live acquisition artifacts separate from later replays.

## What to implement next

1. **Use a staged basic-query fallback.** Try legal-form-free name + city + `Kontakt Impressum`, then a company/contact-specific query. The first fallback contributed six audited additions; the paired alternate query contributed two further additions using basic depth as well as advanced. Reserve it for unresolved companies to control spend.
2. **Improve owner verification before relaxing brand matching.** Visit the selected result, homepage and ranked imprint/contact links. Require a consistent owner or branch statement with source address, exact phone or exact email. Use compact brand spelling as a lead signal. The broader prototype contributed one safe addition at zero search-credit cost but also one false match and one related-entity ambiguity.
3. **Search records without city/postcode, while keeping acceptance strict.** Forty records are omitted by the current search gate. A regional hint can find candidates; exact source phone confirmed Glaser even without source city. Better source-address coverage, nearest-address locality hints or own-site coordinates are further hypotheses, not measured recovery gains.
4. **Treat directories and JSON-LD as candidate sources.** Extract the owned website link, then verify its operator. Neither tested directory traversal nor exact JSON-LD profile matching added a safe homepage here. Do not prioritize directory-hostname expansion as an automatic acceptance shortcut.
5. **Give related entities and rebrands a review queue.** Check legal owner, parent/branch relationships, source email and register information before merging identities. Vi2vi demonstrates why history pages alone are insufficient. This can reduce unknowns while retaining provenance.
6. **Investigate rendering only for allowed, accessible HTML shells.** Use browser rendering or extraction tooling in an environment where available, with a matched control set. It has no measured benefit from this study yet.

The production Tavily integration and current strict verifier remain the deployed-code candidate from the earlier work. This study supplies audited evidence for the subsequent guarded production changes; the permissive experimental verifier is not substituted for it.
