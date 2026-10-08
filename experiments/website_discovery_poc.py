"""Compare guessed domains, OSM email leads, and saved search leads with page verification.

Live mode honors robots, pacing and a shared HTTP budget. Offline replay requires
captures from a previous run; cache misses remain unresolved, never trigger HTTP.
"""
import argparse
import copy
import json
from pathlib import Path

from hiring_scraper.http import Client
from hiring_scraper.website_discovery import discover_missing_websites, tokens, verify_lead


def run(cohort, leads, client):
    rows = json.loads(cohort.read_text())
    search_results = json.loads(leads.read_text())['results']
    output = {'cohort_size': len(rows), 'methods': {}}
    # Deliberately simple domain guessing is a competing prototype, not a production source.
    guesses = []
    for row in rows:
        words = sorted(tokens(row['name']))
        if words:
            url = 'https://' + ''.join(words) + '.de/'
            guesses.append(verify_lead(row, url, client, 'guess'))
    output['methods']['domain_guessing'] = guesses
    for method in ('email', 'search', 'combined'):
        candidates = copy.deepcopy(rows)
        if method == 'search':
            for row in candidates:
                row['tags'].pop('email', None)
                row['tags'].pop('contact:email', None)
        for row in candidates:
            # Resolve separately to keep saved results source-keyed, just as production queries are.
            row['website_url'] = None
        decisions = []
        for row in candidates:
            search = None if method == 'email' else lambda q, sid=row['source_id']: search_results.get(sid, [])
            decisions.extend(discover_missing_websites([row], client, search=search))
        output['methods'][method] = decisions
        output.setdefault('resolved', {})[method] = [
            {k: r.get(k) for k in ('source_id', 'name', 'website_url', 'domain_match_method', 'domain_evidence_url')}
            for r in candidates if r.get('website_url')]
    output['requests'] = len(client.records)
    output['summary'] = {method: {'checked': len(decisions),
                                 'accepted': sum(d['accepted'] for d in decisions)}
                         for method, decisions in output['methods'].items()}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, default=Path('data/website-discovery/cohort.json'))
    parser.add_argument('--leads', type=Path, default=Path('data/website-discovery/search-leads.json'))
    parser.add_argument('--captures', type=Path, default=Path('data/website-discovery/http'))
    parser.add_argument('--cache-from', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--out', type=Path, default=Path('reports/website-discovery-poc-results.json'))
    args = parser.parse_args()
    client = Client(args.captures, timeout=6, delay=1, max_requests=150,
                    cache_from=args.cache_from, offline_only=args.offline)
    result = run(args.cohort, args.leads, client)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
