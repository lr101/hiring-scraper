# Career-page discovery POC

**Run date:** 2026-10-05 UTC · **Implementation:** Python standard library, no browser or credentials · **Full validation run:** `data/career-poc/run-08/`

The POC starts with a company homepage, checks a short robots-aware crawl of its career, jobs and sitemap links, finds ATS boards linked from the company, and calls a small allowlist of public read-only job feeds. It can also identify Greenhouse application links on a company-branded careers domain through `gh_jid` without inventing a public board token.

Run it from the repository root:

```bash
python3 -m hiring_scraper \
  --seeds data/career-poc/seeds-full-validation.json \
  --out data/career-poc/my-run \
  --max-pages 6 --max-depth 3 --timeout 12 --max-requests 300
```

Choose a new `--out` directory for every run. The command writes the seeds, code hashes, request metadata, candidate evidence, parsed public listings, company CSV and summary. `--cache-from data/career-poc/run-08/http` verifies saved body hashes, copies captured pages, and only fetches missing URLs. Replayed results retain their original capture time. It is useful for repeatable parsing experiments, not a fresh uptime check.

## What was measured

The first 12 seeds are the same OSM `office=it` sample used in the Karlsruhe study. The final run reports career content or a readable public job feed for **6 of 12**. The other six were unresolved within the six-page limit, were inaccessible, or lacked links the heuristic could follow. This is a small, ordered IT sample, not a Germany-wide accuracy or coverage estimate.

The full run has 25 deliberately mixed seeds: the first 20 are the original OSM/Karlsruhe/search controls, and the final five add an ATS-linked company homepage, a direct vendor board control, and extra Greenhouse-focused pages. It reports:

| Run outcome | Companies | Meaning |
|---|---:|---|
| Jobs feed found | 6 | A supported public feed parsed successfully; see provider counts below. This can include a complete empty feed. |
| Career content found | 10 | A career or job page matched the title/heading and recruitment-content heuristic. No compatible feed was extracted. |
| Unresolved | 9 | The run found no confirmed career page/feed within limits, or HTTP/robots access failed. This is not proof that no careers site exists. |

The 147 distinct URL captures in run-08 used 143 verified local captures from run-02 and fetched four new URLs. The run includes all 25 seeds and the latest code, and its recorded source hashes match the source files. Its six feeds contained 64 postings across four non-empty feeds; two parsed feeds were empty. Raw `.body` files are gitignored. The code and derived output are committed, but a fresh clone cannot independently inspect the original HTML. Keep the local capture directories beside the report if you need to audit this dated experiment. Run manifests record the code SHA-256 values used.

A focused Greenhouse pass for trivago processed 13 captures (10 reused and 3 fetched) and found its `/apply?gh_jid=…` link. That exposes a Greenhouse-backed application flow on the company's own careers domain, but not a board token or public feed. Its result is in `data/career-poc/run-06/` and is included as evidence in the full validation run, but does not produce a feed result.

## Live hosted-platform checks

| Provider | How the POC identified it | Live feed result | Limit |
|---|---|---:|---|
| Greenhouse | Company link to its branded `job-boards.greenhouse.io` board; also identifies custom-domain `gh_jid` application routes | Separate Wooga board control: 3 jobs | A custom-domain `gh_jid` route identifies the provider, but does not reveal a board token/feed. |
| Lever | Packmatic company homepage links to the hosted Lever board | Packmatic: 13 jobs | 100-item response limit; exactly 100 is reported incomplete because pagination is not implemented. |
| Personio | TelemaxX and Chrono24 career pages link to hosted Personio boards; Cinemo's careers-page CSP names its Personio tenant | 10, 23 and 0 jobs | Personio's XML feed must be enabled by each employer. An empty but valid feed differs from a fetch or parse failure. |
| Ashby | DeepL/Ecosia company careers pages link to their Ashby boards | 18 and 0 jobs | Public feed returns listed postings; the test does not independently verify every job's location. |

These are unauthenticated, read-only feed integrations: Greenhouse [documents public GET access](https://docs.greenhouse.io/job-board.html); Lever publishes its [postings API](https://github.com/lever/postings-api); Personio describes the employer-enabled [XML job feed](https://support.personio.de/hc/en-us/articles/207576365-Integrate-jobs-from-Personio-into-your-website-via-XML); and Ashby documents its [public job-posting API](https://developers.ashbyhq.com/docs/public-job-posting-api). API requests are separate from HTML crawling, so a failed `/robots.txt` request is not treated as proof that a documented API endpoint is blocked. API HTTP status and schema still control the observed result.

The finder recognizes Workday, SuccessFactors, Softgarden, Recruitee, Helix, Onlyfy and SmartRecruiters URL patterns as **possible provider links**. It does not claim a feed for those systems. The run exposed why URL matching needs care: SuccessFactors JavaScript assets looked like boards under the first matcher, so the final matcher ignores assets and requires a career-route pattern and employer parameter for SuccessFactors. Detection-only links need human confirmation before being stored as an employer's active ATS.

## Problems found

- **Page limits miss real sites.** The six-page cap is reached by sites with many language switchers, category links, jobs and sitemaps. For example, trivago's careers links are present, but identifying Greenhouse required a separate ten-page focused run.
- **Heuristics miss unfamiliar wording.** ISCL's page says “Finde deinen Traumjob” and “Werde Teil von ISCL”. Adding those phrases changed its result from unresolved to career content found. A language-model-free keyword list will continue to miss new wording.
- **False positives need a placeholder check.** MegaCycle has a valid `Stellenangebote` URL but only template placeholder text; final classification leaves it unresolved. The homepage “Karriere” teaser is not counted as a career page by itself.
- **Failures are ambiguous.** PDMLab's robots request timed out, Excipio had DNS failure, Technidata's robots URL redirected to HTML, Contentful's robots request returned 429, and ResearchGate's returned 403. These indicate this client's access at test time, not that the companies have no hiring site. No challenge was bypassed.
- **External links need provenance and judgment.** Following a company's career-labelled external link finds rebranded domains and hosted ATS boards. Each result records the referring page and link. A link is evidence of a relationship, not legal proof of who owns the account; arbitrary non-ATS external career links can still consume the crawl budget.
- **Zero jobs is not a failed scrape.** Cinemo and Ecosia returned valid empty feeds during this run. Keep `feed_state`, job count and completeness separate; do not report them as HTTP failures or silently mark the company as not hiring.
- **Locations need job-level interpretation.** A listing can have multiple offices, remote locations, or a shared job title with locations outside Karlsruhe. The POC preserves provider locations but does not robustly normalize them to a search radius.
- **Redirects and robots affect coverage.** The HTTP client checks robots before every HTML redirect hop, limits requests and rejects non-public IP addresses. ATS feeds have a separate exact host/path allowlist. This is still a prototype; the DNS check is vulnerable to rebinding between validation and connection, and robots handling has not been validated against every edge case.
- **Company address and posting location are different.** Keep office evidence, careers URL and job location as separate records. A Berlin or Germany-wide company can employ remotely or advertise in Karlsruhe without having a Karlsruhe office.

## Recommendation

The company-homepage path works well when a site directly links its careers page or a supported ATS board: Packmatic gave the cleanest Lever example, while TelemaxX, Chrono24 and DeepL surfaced feeds through their company career pages. OSM is useful for seeds, but its stale/missing URLs left unresolved cases. Before scheduling this at larger scale, add configurable page budgets, locale/link deduplication, more conservative external-domain handling, provider-specific feed pagination, job-location normalization, a larger stratified sample and periodic source-health checks.

This is discovery evidence, not a continuously maintained job inventory. The next job-monitor step should save stable provider job IDs, first/last-seen timestamps and successful-complete-fetch state so that “new” and “disappeared” jobs are not inferred from partial or failed feeds.
