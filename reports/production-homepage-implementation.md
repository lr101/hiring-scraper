# Production homepage changes

The production resolver now uses the three recommended improvements from the expanded PoC:

- Full-name/basic search, followed by legal-form-free contact/imprint and official-website queries for unresolved companies. Query passes share the existing total search budget and reuse identical queries within a run. Each non-final pass reserves roughly a third of its remaining calls for later strategies, so large batches can actually reach the fallbacks.
- Up to four same-host pages per lead, ranked toward imprint, homepage and contact pages. Current legal-owner declarations take precedence over historical company mentions. Navigation/footer text, imprint branding titles/headings and legal boilerplate do not supply operator identity. Related entities, unavailable known imprints and competing verified domains remain unresolved.
- Search without source city/postcode. The nearest city tagged on a mapped candidate supplies a regional query hint; it is not written into source tags or used as acceptance evidence. Same-page source address, complete phone number or email corroborates the broader owner check. Compact brand spelling and uppercase acronyms can identify owned domains; generic industry words cannot.

The default caps remain 100 missing companies, 50 basic searches and 180 website request attempts per job. Search stops when the crawl cap is exhausted. Advanced search, directory acceptance and JSON-LD-only approval are not enabled. Existing homepage values remain preserved.

Fallback provenance is persisted using `web_search_verified_contact_fallback` or `web_search_verified_official_fallback`, with the accepted ownership evidence URL. Existing exact-search and email provenance values remain supported. No database migration is required.

## Validation

Regression cases cover query fallbacks, fair budget reservation in large batches, exhaustion, locality hints, complete-phone matching, generic industry domains, competing domains and current owner versus historical footer/title/H1–H6 text. All 401 tests passed; Python compilation and whitespace checks passed. Independent code review reported no critical or important remaining findings.

`experiments.production_homepage_replay` exercises the current resolver using saved **basic** search responses and page captures. Unknown queries remain explicitly unavailable; this is a partial captured-evidence regression exercise, not a new full live search benchmark. The stronger policy need not reproduce every manually accepted PoC association.

During implementation, 151 bounded website request attempts captured additional ownership pages; **no new search credits** were spent. The final replay is offline. The known Stober directory and related Vi2vi entity are excluded. An additional Autobahn association is supported by the organization's own branch page: Außenstelle Karlsruhe, Durlacher Allee 77, 76131 Karlsruhe, matching the source address. Its parent-organization homepage is the appropriate scope.

Results and decisions are in `production-homepage-replay-results.json`; acquisition cost provenance is retained separately in `production-homepage-recapture-results.json`. Historical PoC results remain separate. Ignored `data/website-discovery/expanded/production-http/` captures are required to replay the additional ownership-page checks in another workspace.

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m experiments.production_homepage_replay
```

Deployment has not been performed. A manual discovery job on an existing search area will use the new resolver after the updated application is deployed.
