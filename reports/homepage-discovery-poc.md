# Homepage discovery POC

**Observed:** 2026-10-06 UTC. **Cohort:** the saved 2,251-record Karlsruhe OSM snapshot in `fixtures/karlsruhe-osm-candidates.csv`; 645 records have no `website` value. This POC used saved OSM/Wikidata/BA/TED material before a small robots-aware homepage check. It used no credentials, browser automation, or access-control bypass.

## Baseline and result

The existing OSM QID/brand/operator → Wikidata P856 resolver and name-matched `url=*` baseline selected 13 unique domains. The earlier manual Mail & Media association therefore made 14 known homepage records in the dated fixture.

The direct-email method selects **7 additional unique domains**, taking the automatic total from 13 to **20** and the known-domain count including Mail & Media from 14 to **21**. The POC preview applies exactly those seven values and changes `applied_count` from 13 to 20; it does not modify the production fixture.

| Method | Saved inputs inspected | Result for 645 missing-website candidates | Decision |
|---|---:|---:|---|
| Existing OSM QID/brand/operator P856 and `url=*` | 81 entities; 86 suggestions | 13 selected domains | Retained baseline. |
| Wikidata direct-coordinate names plus P856 | 62 point-candidate rows | 0 name-overlap (>=0.8), within-1-km matches | No promotion. |
| Direct OSM `email` / `contact:email` domain | 40 email-bearing records | 10 raw exact-name candidates; 7 unique after stronger selections win | Promoted with strict rules. |
| Saved BA jobs and TED winner samples | 25 BA; 25 TED rows | 0 exact match to a website-less OSM name | Not a homepage resolver. |
| OSM contact URL variants | All 645 matching OSM elements | 0 `contact:website`, `contact:url`, `website:de`, or `website:en`; 3 `url=*` values were already evaluated | No new code. |

## Reusable email-domain resolver

[`hiring_scraper/osm_websites.py`](../hiring_scraper/osm_websites.py) now has `resolve_osm_email_websites`. It derives a homepage root only from an email attached to the same OSM candidate, retains every parseable email domain as an auditable suggestion, and records the OSM element URL, match score, method, and ineligibility reason.

Automatic enrichment requires a non-free email domain, no existing candidate using that domain, and candidate/domain token evidence at least 0.8. Partial cases such as MatSec → `matsec-sec.de` and Stober Metallbau → `stober-mw.com` stay review-only. Free-mail and already mapped values are also retained but not selected. Direct QID/brand/operator P856 and name-matched OSM URL evidence rank ahead of email, so this method cannot replace a stronger source.

The seven preview additions are Autobahn GmbH Außenstelle Karlsruhe → `autobahn.de`, R3DT GmbH → `r3dt.com`, Joas Immobilien → `joas-immobilien.de`, Buderus → `buderus.de`, a3architekten → `a3architekten.com`, kr3m media → `kr3m.com`, and Schrifthof → `schrifthof.de`. See [`suggestions.csv`](../data/location-sources/homepage-poc-2026-10/suggestions.csv) and the compact [`summary.json`](../data/location-sources/homepage-poc-2026-10/summary.json).

## Precision check and access behavior

One validation pass covered the original nine email/name candidates (the seven strict candidates plus the two later-demoted partial matches), using the existing `Client`: 12-second timeout, one-second per-origin pacing, 45-request cap, and robots checks on every hop. It made 23 requests: 13 HTTP 200, six redirects, one 404, and three network/TLS/DNS failures. Six of the seven strict candidates returned public HTML whose title/body corroborated the mapped identity. R3DT redirected to `xr-easy.com`, whose body still names R3DT; Autobahn showed its parent Die Autobahn GmbH. No fetched strict candidate was a false identity match (6/6 directly checkable).

`joas-immobilien.de` could not be checked because its HTTPS robots request failed a TLS handshake. MatSec had DNS failure and Stober had TLS failure; both were already review-only because their name/domain scores were partial. These are access observations, not evidence that a domain is absent. Local capture metadata is in `data/location-sources/homepage-poc-2026-10/email-domain-http/`; there were no retries, proxies, authentication, or challenge handling.

## Reproduce

This reuses the saved 81-entity cache and makes no network request:

```bash
.venv/bin/python -m experiments.enrich_osm_websites \
  --entity-cache data/location-sources/run-2026-10-05/wikidata-osm-website-entities.json \
  --suggestions data/location-sources/homepage-poc-2026-10/suggestions.csv \
  --enrichment-in fixtures/karlsruhe-career-enrichment.json \
  --enrichment-out data/location-sources/homepage-poc-2026-10/enrichment-preview.json
```

The resolver proves a homepage lead, not that the site is live, current, a distinct legal entity, or hiring. The normal bounded crawler remains responsible for robots-aware access and careers discovery. This Karlsruhe snapshot is not a nationwide coverage estimate.
