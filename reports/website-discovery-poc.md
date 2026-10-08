# Missing company website discovery

Observed 2026-10-08. The Karlsruhe snapshot has 2,251 companies, including 645
without websites. Production previously used only direct OSM tags, linked Wikidata
P856 claims, and name-matched `url` tags. It did not use the email resolver or search.
The [earlier experiment](homepage-discovery-poc.md) found six manually verified
email domains but required externally supplied approval evidence.

## Compared prototypes

The new [14-company cohort](../data/website-discovery/cohort.json) intentionally
includes email leads, companies without email, generic names, inaccessible sites,
and stale or ambiguous identities. It is a diagnostic sample, not a random sample
or a coverage estimate for the 645 missing websites. Names come from the saved
website-less OSM fixture. Public address/contact tags were fetched from the OSM
nodes API on the observation date, separately from the saved snapshot. These
public inputs and [search leads](../data/website-discovery/search-leads.json) are
retained to make the inputs reviewable.

| Prototype | Leads checked with final verifier | Accepted companies |
| --- | ---: | ---: |
| Guess a `.de` domain from company-name tokens | 14 | 0 |
| OSM email domain + public page verification | 7 | 3 |
| Name/location web search leads + public page verification | 5 | 1 |
| Email first, then search | 12 | 4 |

The [results](website-discovery-poc-results.json) contain source IDs, URLs, decisions,
rejection reasons, and accepted evidence URLs. The three email additions are kr3m,
a3architekten, and Schrifthof; search adds Curious About. The final resolver accepted
four of 14 companies that lacked websites in the original fixture. Each accepted
page was inspected during the experiment; no false match was found among these
four. This small selected cohort does not establish a general precision rate.

The search prototype uses manually recorded web-search leads, including official
pages such as [Curious About](https://curious-about.xyz/). It is **not** a live Brave
benchmark. The Brave adapter uses the [documented web search API](https://api-dashboard.search.brave.com/app/documentation/web-search/codes),
whose authentication and response parsing are covered by local transport tests.
No Brave credential was available, so provider-specific live result quality, costs,
and rate limits have not been measured. Search can be enabled independently without
changing verification rules.

## What the experiments exposed

- Direct Google HTML returned HTTP 200 with a JavaScript/challenge page; DuckDuckGo
  HTML returned HTTP 202 with a challenge. Neither yielded usable search results.
  [Probe metadata](../data/website-discovery/search-html-probes.json) records these
  observations. Neither HTML scraper is used in production.
- Domain guessing found accessible but incorrect sites: `a3architekten.de` is a
  different architecture firm, and `joas.de` is unrelated. Some guesses redirect
  to the right brand, but guessing provides no source-linked ownership evidence.
- `consultive.net` and `consultive.ch` identify other locations. Name similarity
  alone cannot establish a Karlsruhe company match.
- The initial verifier accepted the vi2vi group contact page, but inspection showed
  that it names **vi2vi Retail Solution GmbH**, not the mapped **Vi2vi GmbH**, and a
  different street. A failing regression test reproduced this error. The final
  verifier requires the mapped legal name as a phrase for search matches and
  rejects this lead. The group's [contact page](https://vi2vi.com/kontakt/) is
  retained as a rejected probe, not an approved homepage.
- R3DT's email domain redirects to XR-EASY. The homepage mentions R3DT but does not
  provide enough old-name and local evidence under the final policy. It remains
  unresolved rather than inheriting trust merely from a redirect.
- MatSec, Joas, Persona Concept, and Mosaik could not be verified because robots,
  DNS, or TLS access was unavailable. These are access failures, not proof of absence.
- PROMATIS and ORGAKOM have search leads but their sampled OSM records lack address
  tags. Without a source-linked locality clue, the final resolver leaves them
  unresolved. Generic Center Management has no usable identity evidence.

The [exploratory probes](../data/website-discovery/exploratory-probes.json) retain the
initial looser observations; their `identity_verified` field is an exploratory
heuristic, not a production acceptance decision. Only the final results above
represent the implemented verifier.

## Production choice

Keep existing OSM/Wikidata evidence first, then verify non-free email domains from
that same map record. When `TAVILY_API_KEY` (preferred) or `BRAVE_SEARCH_API_KEY` is configured, search still-missing
companies by quoted name and mapped postcode/city. Search snippets are leads;
they cannot approve a website. Fetch at most three distinct lead hosts and, for
each, at most one linked contact/imprint page through the existing robots-aware
HTTP client. The client checks public destinations at every hop, caps response
sizes, and enforces pacing and a shared request budget.

Identity checks require distinctive company tokens, a matching company-brand domain and page heading, and, for search/cross-domain redirects, local corroboration. One-token
names need postcode or street evidence. Explicit legal names must match the page's
legal-name phrase for both email and search. Email matches also require all distinctive
primary-name tokens in the title or H1; a descriptive subtitle separated by a spaced
dash is checked in the body but is not part of that heading requirement. A regression
fixture rejects Northstar Robotics when the destination identifies Northstar Inc,
even if its body mentions Northstar Robotics as a client. Free-email services, common directories/social/job portals,
non-public URLs, parked domains, and conflicting verified domains are rejected.
The automatic rule intentionally sacrifices recall for uncertain names, ownership,
renames, and missing addresses. Search results on unrelated domains are rejected even when their page title and
address match, because directories and investor articles can reproduce both.
This deterministic heuristic does not prove legal ownership.

The resolver fills only missing website fields and stores its method and actual
verification page in existing domain-evidence columns. Normal career discovery
then handles the new homepages. No migration is needed. Search/verification errors
leave the company unresolved and preserve direct map results. A search-provider failure
turns off API searches for the rest of that job; credentials are never saved to
captures or forwarded through HTTP redirects.

The default limits are 100 missing companies (email-bearing entries first), 50
search queries, and 180 website HTTP requests, including robots and redirects.
They can be adjusted in `.env`; zero searches disables search, and zero companies
disables this new enrichment stage. Caps mean a large area may retain missing
websites; repeating the same inputs does not automatically rotate the cohort.
Recurring feed refreshes do not run homepage discovery. Use a manual discovery
job to backfill existing companies.

## Tavily live follow-up

The [Tavily live benchmark](tavily-website-discovery-poc-results.json) used the same
14-company cohort with **8 basic searches costing 8 credits**. Email verification
accepted three companies before search; Tavily supplied the verified Curious About
homepage, for four accepted companies total. The final result was replayed offline
against the exact saved API URL leads and page captures after tightening ownership
checks; the replay spent no additional credits. `credits_used` in the artifact
records the original live query cost, while `offline_verification_replay` identifies
the final verification replay. Local request capture reuse also reduces repeated
website fetches; this was not a completely fresh crawl.

The initial run exposed false positives on `agentur.de` (Mosaik's directory profile)
and `htgf.de` (an R3DT investor article). Both named the company and its local address,
so the previous title/address fallback was insufficient ownership evidence. Two
failing regression cases reproduced these errors. The final verifier requires a
matching company-brand domain for search results and rejects both; brand renames
on unrelated domains remain unresolved. Results and snippets alone are never used
to approve homepages.

The key is stored only in the ignored private `.env`, passed through Compose as
`TAVILY_API_KEY`, and sent to the fixed Tavily endpoint as a Bearer token. The adapter
uses [basic search with automatic parameters disabled](https://docs.tavily.com/documentation/api-reference/endpoint/search),
five results, and no generated answer or raw-content requests. The existing query
cap applies; auth, quota, rate-limit and malformed-response errors disable Tavily
for that job, without automatically spending credits at another provider. Brave
remains available when no Tavily key is configured.

Reproduce the live benchmark (up to 14 basic-search requests):

```bash
.venv/bin/python -m experiments.tavily_website_discovery_poc
```

Replay saved Tavily leads and local captures with zero API requests:

```bash
.venv/bin/python -m experiments.tavily_website_discovery_poc \
  --replay-from reports/tavily-website-discovery-poc-results.json \
  --cache-from data/website-discovery/tavily-http \
  --captures /tmp/tavily-replay --out /tmp/tavily-replay-results.json
```

## Reproduce

Live robots-aware comparison (network-dependent results may change):

```bash
mise exec -- uv sync --locked
.venv/bin/python -m experiments.website_discovery_poc
```

Replay locally saved captures without any HTTP:

```bash
.venv/bin/python -m experiments.website_discovery_poc \
  --offline --cache-from data/website-discovery/http \
  --captures /tmp/homepage-replay --out /tmp/homepage-replay-results.json
```

Raw captures stay local and are ignored by git; committed inputs and decision
metadata are available in a fresh checkout, while exact offline replay requires
captures from a live run. `--offline` on an empty cache reports unresolved leads.

Tests:

```bash
.venv/bin/python -m unittest discover -s tests
```
