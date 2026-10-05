# Hiring website discovery — Karlsruhe

A research prototype for discovering employers near a German location and finding their own career websites. First live study: **Karlsruhe, 5 km around 49.0069, 8.4037**, on **2026-10-05 UTC**.

Start with [the evaluation](reports/karlsruhe-evaluation.md). The recommendation is OSM + regional employer directories for company discovery, Arbeitsagentur for evidence of active hiring, and search to resolve missing career URLs.

## Results and evidence

- [Manually checked career websites](data/karlsruhe/verified_career_sites.csv)
- [OSM candidates](data/karlsruhe/osm_candidates.csv): 1,453 mapped objects; 1,080 with website tags, not 1,453 verified companies.
- [KIT directory candidates](data/karlsruhe/kit_candidates.csv): includes employers outside Karlsruhe; filter the location column.
- [Arbeitsagentur sample](data/karlsruhe/ba_candidates.csv): one API page, not an exhaustive export.
- [Machine-readable counts](data/karlsruhe/summary.json), [12-site OSM experiment](data/karlsruhe/career_sample_results.json), and [search-assisted checks](data/karlsruhe/search_results.json).

Each HTTP request has JSON metadata containing UTC time, URL, status/error, final URL, size, and body hash. Full `.body` responses remain in this workspace but are gitignored to avoid committing entire third-party pages. A fresh clone must run the acquisition commands before offline analysis, which observes a changed web rather than reproducing the dated study. The original manual content claims cannot be independently audited from Git alone; preserve the local `.body` captures for that purpose. Search seeds were selected during interactive research; raw search result sets/rankings were not archived, and automated search access was not evaluated. Initial Leipzig preflight requests in `data/probes` predate the user's Karlsruhe selection and are excluded from the study.

## Run

Python 3.11+, standard library only. Run from the repository root.

```bash
# Makes seven bounded source/robots requests, with one-second pauses.
python3 experiments/discover.py
# Offline: recreate candidate CSVs and counts from the downloaded responses.
python3 experiments/analyze.py
# Makes robots/homepage/career requests for a fixed OSM sample (roughly 2 minutes).
python3 experiments/careers.py
# Check six manually curated search hits; this does not query a search engine.
python3 experiments/search_check.py
# Regression check for empty career navigation links.
python3 -m unittest discover -s tests -v
```

`discover.py --city ... --lat ... --lon ... --radius-m ... --out ...` changes the BA/OSM search. Supply matching city and coordinates; there is no geocoding. The directory probes, downstream scripts, curated seeds, and report are specific to the Karlsruhe study. Reruns overwrite the selected output paths; preserve a copy for historical comparisons. The BA detail probe metadata records the additional one-off request made during research.

## Scope

These are bounded feasibility experiments, not a production crawler. They do not schedule checks, extract a complete job inventory, send applications, or notify anyone. Career detection uses homepage links and requires human confirmation. HTTP 200 alone is not a successful job extraction. No browser rendering, account login, CAPTCHA solving, proxy rotation, or challenge bypass was used.

Before recurring use: add per-origin scheduling and backoff, caching and conditional requests, robust robots handling across redirects, domain/branch normalization, ATS-specific job extraction, and stable job IDs with first/last-seen timestamps. The current generic HTTP probe follows redirects automatically and is intended for known public research URLs, not untrusted user input.

## Data attribution

OSM-derived data is © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under ODbL. Preserve attribution and assess the applicable database sharing requirements when distributing derived databases. Other source data retains its source rights; this repository does not grant a license to third-party content. Successful HTTP access is an observation, not a determination of reuse permission.

## Career-page discovery POC

The follow-up crawler starts with a company homepage, follows bounded career links and sitemaps, and supports public Greenhouse, Lever, Personio and Ashby feeds. It also detects hosted Workday, SuccessFactors, Softgarden, Recruitee, Helix, Onlyfy and SmartRecruiters URLs without claiming unimplemented feeds. Run instructions, live results and limitations are in [the POC evaluation](reports/career-discovery-poc.md).

Example:

```bash
python3 -m hiring_scraper --seeds data/career-poc/seeds-full-validation.json --out data/career-poc/my-run
```

Runs require a new output directory. The input seed lists and result manifests make the scope visible. Body captures stay local and are omitted from Git; reuse them with `--cache-from` for deterministic parsing experiments.
