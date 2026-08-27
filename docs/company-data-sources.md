# Company and location source research

Research date: 2026-08-26.

## Alpha decision

The alpha accepts a company domain directly. The company's own HTTPS website is the authoritative
source for the display name, website, career link, and job postings. On first submission the app
fetches the homepage, checks Organization JSON-LD and standard metadata, scores career links, and
creates a generic JSON-LD career source. The collector follows only a bounded set of HTTPS
career/job links on the submitted site or the explicitly discovered career host and parses
`JobPosting` JSON-LD. This gives the user dynamic discovery without a bulk company import or a
credential requirement.

## Company databases and directories considered

- [OpenCorporates API](https://api.opencorporates.com/documentation/API-Reference) is a strong
  legal-entity registry with registered addresses and occasional website data. It is useful for
  future legal-name or address enrichment, but it needs an API key and is company-name/search
  oriented rather than a dependable domain-first job source.
- [Wikidata data access](https://www.wikidata.org/wiki/Help:Data_access) can provide curated
  headquarters and official-website links through the API or
  [SPARQL](https://query.wikidata.org/), but coverage and freshness are uneven. Its own guidance
  makes it a poor replacement for fuzzy domain search or large unbounded queries.
- [GLEIF's API](https://www.gleif.org/en/lei-data/gleif-api) offers high-quality legal-entity and
  ownership data, but only for entities with LEIs. It is an enrichment option, not a general
  employer or career-site directory.
- [Google Places Text Search](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places/searchText)
  can return a place's website URI and strong local business matches, but it requires Google
  authentication, billing, field masks, and provider-specific terms. It is a future opt-in
  enrichment source, not the default alpha dependency.

These sources should remain optional enrichment adapters. They do not replace the submitted
website as the operational job source, and no API credentials are needed for the current flow.

## Location APIs considered

- [Nominatim Search](https://nominatim.org/release-docs/latest/api/Search/) is the default because
  it supports user-entered free-text and structured place searches and returns coordinates and
  address details. The app calls it only from an explicit Search button, caches results, sends an
  identifying User-Agent, limits requests to Germany, and displays
  [OpenStreetMap attribution](https://www.openstreetmap.org/copyright).
- The [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) limits
  the public service to one request per second and disallows client-side autocomplete and bulk
  geocoding. The implementation therefore uses a one-request-per-second process-wide throttle,
  database caching, and a configurable `LOCATION_API_URL` so a self-hosted or commercial
  Nominatim-compatible service can be substituted.
- [Google Places Text Search](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places/searchText)
  is a future paid alternative when richer place coverage or guaranteed quota is required.
- [Overpass](https://dev.overpass-api.de/overpass-doc/en/) is useful for spatial OpenStreetMap tag
  queries, but it is not the right default geocoder for a user's city or postal-code lookup.

The old GeoNames ZIP importer and unauthenticated place-search endpoints are removed. Places are
now created on demand from a user search and retained as cached database records for profiles and
monitoring targets.

Migration `0007` normalizes existing domains, keeps the oldest company row for duplicate domains,
repoints monitoring targets and non-conflicting sources, and merges duplicate source jobs while
preserving the older job record and its user state where possible. This deterministic policy lets
existing databases adopt the unique canonical domain field without an operator-run import.
