# Karlsruhe IT follow-up: 20 OSM companies

**Observed:** 2026-10-05 UTC  
**Sample:** the 20 nearest OSM `category=it` records with website tags that did not already have an enrichment record. They are 531–1,925 m from the Karlsruhe center. This is a deliberately focused engineering-company sample, not a random or Germany-wide coverage estimate.

## Result

The original run, `data/career-poc/run-14/`, found 9 career-content pages, 11 unresolved companies, and no new feeds. The final implementation replay in `data/career-poc/run-20/` classifies 9 career pages, 10 unresolved companies, and 1 parsed feed. It used 143 saved captures and made two new bounded requests; the preceding live follow-up in `run-18` fetched seven URLs that were missing from the earlier 20-company crawl.

The new feed is IONOS Greenhouse: **37 complete feed rows**, including **17 rows with Karlsruhe as an extracted locality**. The 37 rows represent 26 requisition IDs across the whole board. The company is already in OSM with a direct IONOS website tag; the app now records its company-branded careers URL, Greenhouse tenant, and parsed jobs. The app’s Karlsruhe job filter returns all 17 Karlsruhe IONOS rows.

| Company | Website in OSM | Official career/job page | Finding and stop reason |
|---|---|---|---|
| Audials | [audials.com](https://audials.com/) | [Audials careers](https://audials.com/de/unternehmen-audials-ag/karriere) | The homepage and the directly checked career page both return HTTP 403 to the research client. Search indexing exposes the official page, but its content is unavailable to this bot; no feed was parsed. |
| qwertiko GmbH | [qwertiko.de](https://www.qwertiko.de/) | [Jobs](https://www.qwertiko.de/jobs/) | The page and two job-detail pages are reachable and list Linux administrator roles. They are ordinary company-hosted WordPress HTML pages, with no supported feed or ATS signature. |
| Navigate AG | [navigate.de](https://navigate.de/) | No company hiring page verified | The homepage links to agency/team material, not hiring. `/karriere`, `/careers`, and a direct `/jobs` check return 404; the sitemap crawl did not reveal a job page. No feed evidence. |
| awesome | [awesome-it.de](https://awesome-it.de/) | [Careers](https://awesome-it.de/de/careers) | The company-branded page is reachable and describes roles and applications in static Next.js HTML. It exposes no supported ATS or machine-readable feed. |
| ACT Smartware | [act-smartware.de](https://www.act-smartware.de/) | No company hiring page verified | The homepage and sitemap are reachable. `/karriere` and `/jobs` redirect to the same ordinary homepage, with no job content or feed. Search results for similarly named ACT companies were not treated as a match. |
| adesso | [adesso.de](https://www.adesso.de/) | [adesso careers](https://www.adesso.de/de/jobs-karriere/start/index-2.jsp) · [job portal](https://jobs.adesso-group.com/) | The job portal sitemap links to job-detail pages, but its custom portal is not one of the supported feed providers and the pages expose no parsed public feed. An application route encountered during the portal crawl is disallowed by robots.txt. |
| IONOS | [ionos.de](https://www.ionos.de/) | [IONOS jobs](https://jobs.ionos.de/karriere/jobs/alle-jobs) | **New parsed feed.** The old crawl spent its page budget on the related United Internet portal before IONOS’s own job list. It also ranked `/jobangebot/…` below generic career pages and did not recognize `job-boards.eu.greenhouse.io`. The crawl now prioritizes first-party job-list routes, recognizes the German `jobangebot` detail route and Greenhouse EU host, and finds the feed in four pages. |
| Tooltec IT GmbH | [tooltec.de](https://tooltec.de/) | [Karriere](https://tooltec.de/karriere/) | The page is reachable and has a direct “apply” link, but the address is an email-protection route that returns 404 to this client. No ATS or public feed. |
| DatenBerg GmbH | [datenberg.eu](https://datenberg.eu/) | No company hiring page verified | The homepage and sitemap expose no careers page; a direct `/jobs` check returns 404. Search surfaced a LinkedIn working-student posting from about a year earlier that is closed, not a current company feed. |
| Renumics GmbH | [renumics.com](https://renumics.com/) | No company hiring page verified | The homepage links to About and Contact. The standard `/karriere`, `/careers`, and `/jobs` routes return 404, and no supported ATS appears in the reachable pages. |
| dipano GmbH | [dipano.com](https://dipano.com/) | No company hiring page verified | No hiring link is present. Standard career/job path guesses return the ordinary product homepage rather than a hiring page; no feed evidence. |
| emmtrix Technologies GmbH | [emmtrix.com](https://www.emmtrix.com/) | [Jobs](https://www.emmtrix.com/company/jobs) · [compiler role](https://www.emmtrix.com/company/jobs/software-engineer-compiler-construction) | The company jobs page and a linked role page are reachable, but they are employer-hosted pages with no supported ATS or feed link. |
| heliopas.ai GmbH | [heliopas.ai](https://heliopas.ai/) | [Jobs](https://heliopas.ai/jobs/) · [linked vacancy PDF](https://drive.google.com/file/d/16ZpZz3oEPSD0cEwtXv-mKPBI3uUyR4d1/view?usp=sharing) | The jobs page links to a PDF on Google Drive. The crawler follows the link but only classifies HTML and does not extract structured job metadata from the document; no feed is published there. |
| thingsTHINKING / semantha | [semantha.de](https://www.semantha.de/) | Not verified | The OSM website’s robots endpoint returns HTML instead of a usable robots policy. The client conservatively stops before fetching the homepage, so no hiring page or feed is confirmed. |
| Easy Smart Grid GmbH | [easysg.de](https://www.easysg.de/) | [Careers](https://www.easysg.de/about-us/careers/) | The page contains a working-student/master-thesis role, but the first pass classified it as an ordinary page because “looks for talents” was missing from the English hiring vocabulary. The heuristic now recognizes this wording. No ATS/feed link appears. |
| HS High Stake GmbH | [high-stake.de](https://high-stake.de/) | No company hiring page verified | The homepage exposes a project link but no careers link. `/karriere`, `/careers`, and `/jobs` return 404; no feed evidence. |
| BIN Holding | [binholding.de](https://binholding.de/) | No company hiring page verified | The homepage has no hiring link; sitemap and standard career/job routes return 404. No feed evidence. |
| HS Analysis GmbH | [hs-analysis.com](https://hs-analysis.com/) | [Careers](https://hs-analysis.com/careers/) · [German page](https://hs-analysis.com/karriere/) | A sitemap reveals the English careers page. The German route has placeholder-style content about C#/WPF developers; neither page links to a supported ATS or feed. |
| SL Optimum GmbH | [sl-optimum.com](https://sl-optimum.com/) | No company hiring page verified | The reachable homepage describes its AR/VR work. Standard career/job route checks return 404; no hiring page or feed surfaced. |
| PTV Logistics GmbH | [ptvlogistics.com](https://www.ptvlogistics.com/) | [German jobs](https://www.ptvlogistics.com/de/ueber-ptv-logistics/karriere/jobs) | The page embeds Personio tenant `ptv-logistics`, so provider detection succeeds, but the public XML endpoint returns HTTP 404. This is a feed fetch failure, not a parser returning zero jobs. |

## What changed and what remains

The missed IONOS feed had three interacting causes: the crawl ranked a group careers link ahead of the employer’s own job list; the German `jobangebot` detail route was not treated as a high-priority job; and the EU Greenhouse job-board hostname was absent from the provider matcher. Tests now cover the EU hostname, first-party job-list priority, `jobangebot` links, and the English “looks for talents” wording. Greenhouse locations that contain a German postcode/address expose the locality separately; when the address is only a city and `offices[].name` is a legal entity, the primary city is retained as a structured location. Both cases now survive import into the radius filter.

The PTV case demonstrates why provider detection and feed parsing need separate states: the company page clearly embeds Personio, but a 404 feed is not a valid empty feed. The other reachable hiring pages mostly use static HTML, a proprietary portal, or a PDF. They can still be valuable company/job-page results, but need distinct extraction paths; treating them as supported feeds would invent data. HTTP 403 and unusable robots policy also remain access limits, not evidence that a company has no jobs.

At the time of this run, the Karlsruhe fixture contained **7 structured feed records and 93 feed rows** across six OSM companies. The later HTML extraction replay adds five employer-hosted page sources and 24 extracted roles; see the [career-page extraction evaluation](career-page-extraction-20.md). All nine career-content results in this follow-up cohort lacked a parsed structured feed, including PTV’s Personio 404; ten remained unresolved under the bounded crawl. Those unresolved labels mean “not verified within this crawl,” not “no hiring page exists.”

Reproduce with the checked-in seeds and captures:

```bash
python3 -m hiring_scraper --seeds data/career-poc/seeds-karlsruhe-it-followup-20.json \
  --out data/career-poc/new-run --cache-from data/career-poc/run-18/http
```

Use a new output directory each time. `run-14` is the pre-fix baseline; `run-15` contains targeted IONOS, adesso, and PTV portal checks; `run-18` contains the live missing-page/feed captures; `run-20` replays the final code against those saved responses.
