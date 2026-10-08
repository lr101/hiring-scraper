"""Exercise production matching against saved basic-search leads, without API spend.

Offline by default. --recapture permits at most 180 new website request attempts,
using existing robots and public-destination policy. Acquisition artifacts stay intact.
"""
import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from hiring_scraper.http import Client, RequestBudget
from hiring_scraper.website_discovery import discover_missing_websites, phrase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recapture', action='store_true')
    args = parser.parse_args()
    directory = Path('data/website-discovery/expanded')
    rows = copy.deepcopy(json.loads((directory/'cohort.json').read_text()))
    historic = json.loads((directory/'audited-summary.json').read_text())
    raw = json.loads((directory/'completed-results.json').read_text())
    for row in rows:
        mapped = raw['stages']['mapped_wikidata'].get(row['source_id'])
        row['website_url'] = mapped['website_url'] if mapped else None
    cache = json.loads((directory/'search-cache.json').read_text())
    available = {phrase(v['query']):v['results'] for v in cache.values() if v['depth']=='basic'}
    queries = []
    def search(q):
        present = phrase(q) in available
        queries.append({'query':q,'captured':present})
        return available.get(phrase(q), [])
    captures = directory/'production-http'
    captures.mkdir(exist_ok=True)
    budget = RequestBudget(180)
    with TemporaryDirectory() as replay:
        client = Client(captures if args.recapture else replay, timeout=6, delay=1, max_requests=10000,
                        cache_from=directory/'http', offline_only=not args.recapture, request_budget=budget)
        # Reuse newly captured ownership pages as well as historical PoC captures.
        extra = Client(replay, cache_from=captures, offline_only=True)
        client.cache.update(extra.cache)
        decisions = discover_missing_websites(rows, client, search=search, max_searches=360,
                                              max_companies=120, search_area_hint='Karlsruhe Region')
    resolved = {r['source_id']:r for r in rows if r.get('website_url')}
    previous = set(historic['confirmed_homepages'])
    report = {'observed_at':datetime.now(timezone.utc).isoformat(), 'offline':not args.recapture,
              'corpus_size':len(rows),'recovered':len(resolved), 'search_api_credits_spent':0,
              'query_requests':len(queries),'cached_basic_queries':sum(q['captured'] for q in queries),
              'unavailable_basic_queries':sum(not q['captured'] for q in queries),
              'live_website_attempts':budget.used, 'queries':queries,
              'prior_audited_positives_retained':len(previous & set(resolved)),
              'prior_audited_positives_unresolved':sorted(previous-set(resolved)),
              'new_proposals_requiring_audit':sorted(set(resolved)-previous),
              'decisions':decisions,
              'resolved':[{k:r.get(k) for k in ('source_id','name','website_url','domain_match_method','domain_evidence_url')}
                          for r in resolved.values()]}
    output = Path('reports/production-homepage-replay-results.json')
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in {'queries','decisions','resolved'}},indent=2))


if __name__=='__main__': main()
