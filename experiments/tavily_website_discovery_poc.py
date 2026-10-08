"""Live bounded Tavily benchmark against the saved missing-website cohort.

Loads TAVILY_API_KEY from the environment or private .env. Only URL leads, query
counts, credit usage and page-verification decisions are written to the report.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from hiring_scraper.http import Client
from hiring_scraper.website_discovery import TavilySearch, discover_missing_websites


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, default=Path('data/website-discovery/cohort.json'))
    parser.add_argument('--out', type=Path, default=Path('reports/tavily-website-discovery-poc-results.json'))
    parser.add_argument('--captures', type=Path, default=Path('data/website-discovery/tavily-http'))
    parser.add_argument('--cache-from', type=Path)
    parser.add_argument('--replay-from', type=Path, help='Replay saved queries and page captures without HTTP')
    parser.add_argument('--max-searches', type=int, default=14)
    args = parser.parse_args()
    key = os.getenv('TAVILY_API_KEY') or dotenv_values('.env').get('TAVILY_API_KEY')
    if not key and not args.replay_from:
        parser.error('Set TAVILY_API_KEY in the environment or private .env')
    provider = TavilySearch(key.strip()) if not args.replay_from else None
    replay = json.loads(args.replay_from.read_text()) if args.replay_from else None
    saved_queries = {q['query']: q['results'] for q in replay['queries']} if replay else {}
    queries = []
    def search(query):
        hits = saved_queries.get(query, []) if replay else provider(query)
        queries.append({'query': query, 'results': hits})
        return hits
    rows = json.loads(args.cohort.read_text())
    for row in rows:
        row['website_url'] = None
    client = Client(args.captures, timeout=6, delay=1, max_requests=120, cache_from=args.cache_from,
                    offline_only=bool(replay))
    decisions = discover_missing_websites(rows, client, search=search, max_companies=len(rows),
                                          max_searches=max(0, min(args.max_searches, 50)))
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'provider': 'tavily',
              'cohort_size': len(rows), 'search_depth': 'basic', 'search_queries': len(queries),
              'credits_used': replay['credits_used'] if replay else provider.credits_used,
              'provider_disabled': replay['provider_disabled'] if replay else provider.disabled,
              'offline_verification_replay': bool(replay),
              'requests_or_reused_captures': len(client.records), 'queries': queries,
              'decisions': decisions, 'resolved': [
                  {k: row.get(k) for k in ('source_id', 'name', 'website_url',
                                         'domain_match_method', 'domain_evidence_url')}
                  for row in rows if row.get('website_url')]}
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: result[k] for k in ('cohort_size', 'search_queries', 'credits_used',
                                            'provider_disabled')}, indent=2))
    print('Verified websites:', len(result['resolved']))


if __name__ == '__main__':
    main()
