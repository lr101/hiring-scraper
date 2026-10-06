# Full-crawl domain and HTML follow-up

**Observed:** 2026-10-06 UTC. This follow-up reprocessed the full Karlsruhe 15 km crawl with the current HTML parser, tested missing-domain resolution against saved OSM/Wikidata data, and crawled the resulting website leads. It did not rerun the 2,251-company homepage crawl live.

## Findings

The full OSM sample contains 2,251 candidate establishments. Of these, 1,606 have an OSM `website` tag and 645 do not. OSM objects can also point to Wikidata through `wikidata`, `brand:wikidata`, and `operator:wikidata`; Wikidata's `P856` property records an entity's official website. The property and API are documented by [Wikidata](https://www.wikidata.org/wiki/Property:P856) and its [`wbgetentities` API help](https://www.wikidata.org/w/api.php?action=help&modules=wbgetentities). The OSM wiki documents [brand Wikidata links](https://wiki.openstreetmap.org/wiki/Key:brand:wikidata), [operator Wikidata links](https://wiki.openstreetmap.org/wiki/Key:operator:wikidata), and [`url=*`](https://wiki.openstreetmap.org/wiki/Key:url), whose target may be a general reference rather than the official site.

The implemented resolver queried 81 referenced Wikidata entities in API batches of at most 50. It evaluated 86 official-website or OSM-URL suggestions and selected 13 distinct additional domains for the Karlsruhe candidates:

| Evidence | Selected | Rule |
|---|---:|---|
| Direct OSM `wikidata` entity → P856 | 3 | Identifier is attached directly to the mapped establishment. |
| OSM brand Wikidata → P856 | 9 | Candidate name/domain must overlap the entity's German/English label, alias, or official domain. |
| Name-matched OSM `url=*` | 1 | URL hostname must identify the mapped candidate by name. |
| OSM operator Wikidata → P856 | 0 | The operator sites were already represented by another candidate or did not clear identity checks. |

Selection also suppresses domains already mapped by another candidate, ignores deprecated Wikidata claims and claims carrying a deprecation qualifier, and rejects country-specific non-German websites for local brand/operator records. For example, the Helvetia `.com` host was already mapped and its Spanish and Italian URLs were not assigned to the Karlsruhe office. The old Karlsruhe city website claim carried a deprecation qualifier; the current official domain was already represented by an OSM candidate. A weak `url=*` value is used only when its hostname matches the OSM candidate's name.

The 13 leads were stored in [the career enrichment fixture](../fixtures/karlsruhe-career-enrichment.json) with the method, Wikidata ID/claim rank when applicable, and OSM/Wikidata evidence URLs. A separate earlier manual match for Mail & Media is retained. The 645 records without an OSM website tag now have 14 known homepage records in total, leaving 631 without a known domain. The existing importer schedules newly enriched websites for an immediate first crawl.

## Website crawl PoC

The first live pass tested 18 missing-website leads with a six-page, three-link-level cap and robots checks. It made 123 bounded HTTP requests across the 18 websites. The captured pages were replayed offline with the current parser: 123 captures were reused, **zero network requests** were made, and the 18 results contained 7 career-page signals, 3 parsed HTML sources, and 8 unresolved sites. Five of the original 18 were omitted from the final 13-domain selection because of duplicate mapped domains or country/operator evidence.

Among the 13 selected leads, 6 have a discovered career page, 2 yielded jobs, and 5 remain unresolved. The two job sources are KKH and Lohnsteuerhilfe Baden-Württemberg, with five extracted rows total. KKH's list contains roles in Halle and Köln; Lohnsteuerhilfe's postings identify Ludwigsburg, Herrenberg, and Ravensburg. These locations are outside Karlsruhe's 15 km job radius. Contargo's student-program heading is kept as one **unconfirmed role lead**, not an active posting, because the page does not provide an individual job URL. The saved offline replay is [run 07](../data/location-sources/run-osm-wikidata-websites-replay-07/summary.json); the original live-crawl counts are in [the source run](../data/location-sources/run-osm-wikidata-websites/summary.json).

## Full-crawl HTML reprocessing

The [run 31 summary](../data/career-poc/run-31/summary.json) reprocesses all 1,606 original website seeds from crawl slices 25–28. The current parser reports 669 career-page, parsed-feed, or provider outcomes. It parsed 202 feeds and 1,632 jobs: 42 structured ATS/Schema.org feeds and 160 ordinary HTML sources. The HTML safety filter saw 834 rows, accepted 541, and excluded 293 rows from 55 unverified external pages.

For external HTML, a vendor or job portal is not trusted just because the crawler can read it. The page must be linked from the employer's site or an accepted branded career site; a generic third-party board also needs per-posting `hiringOrganization` evidence matching the employer name or domain. A captured detail page can be reused for a second employer only when that employer independently links to the board and the row names that employer. This recovered a matching VBK posting from a shared transport portal while excluding other operators' rows. Generic HOGAPAGE results with no employer-level evidence remain excluded.

The parser now handles:

- Schema.org `JobPosting` and nested/embedded JSON job arrays without evaluating JavaScript; it preserves `hiringOrganization` for employer validation.
- First-party HTML job links, specific card titles, location fields and city names in posting URLs; list and detail copies merge on their canonical posting URL.
- Detail-page headings and visible open-position sections as a lower-confidence fallback. Evergreen student-program headings outside an explicit vacancy section are retained as unconfirmed role leads rather than jobs.
- Hidden-content suppression for `hidden`, `aria-hidden`, inline `display:none`, and closed Bootstrap modals. This removes BGV's cookie-consent modal title, which otherwise looked like a vacancy.

The parser never executes embedded scripts and normalizes descriptions to text. It rejects malformed or non-HTTP(S) posting URLs. Fetching remains bounded by response size, page/depth limits, robots policy, per-origin pacing, and public-address checks. The frontend renders extracted text as text. The recurring worker applies the same employer-verification rules as the batch finalizer.

For the app snapshot, the 13 new domains added five HTML jobs from KKH and Lohnsteuerhilfe; the unconfirmed Contargo lead was not imported as a job. A fresh SQLite import was run twice and produced the same totals both times: 2,251 companies, 210 feeds, 1,664 job rows, with 1,620 companies having a domain. The default 15 km query returns 2,249 companies (1,615 with domains) and 283 matching jobs (260 non-remote in-area jobs and 23 explicitly remote jobs).

## Reproduction and limits

The cached parser pass is repeatable without network access:

```bash
python -m hiring_scraper \
  --seeds data/location-sources/run-osm-wikidata-websites/seeds.json \
  --out data/location-sources/replay-again \
  --cache-from data/location-sources/run-osm-wikidata-websites/http \
  --offline-only --max-requests 2000 --delay 0
```

Apply the cached career results only to currently selected, host-matching domain leads with:

```bash
python -m experiments.enrich_osm_websites \
  --enrichment-in fixtures/karlsruhe-career-enrichment.json \
  --enrichment-out /tmp/karlsruhe-career-enrichment.json \
  --career-run data/location-sources/replay-again
```

The 13 chosen sites are a narrow high-signal supplement, not evidence that the other 631 missing domains have no website. Wikidata statements and OSM tags can be stale or describe a parent brand; every chosen record therefore retains its evidence and resolution method. Five selected websites did not resolve to a career page in the bounded crawl. JavaScript-only postings that are absent from the delivered HTML or embedded data still need a public feed or a future browser-rendered adapter; the crawler does not bypass bot challenges, logins, or robots rules. This remains a Karlsruhe snapshot, not a nationwide coverage claim.
