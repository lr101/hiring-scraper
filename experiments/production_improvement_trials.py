"""Replay the production improvement pilots without making network requests.

Raw captures and the detail/corpus manifests are local inputs. The output contains
only counts, identities and hashes. Baseline code comes from a trusted local commit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hiring_scraper import ats, matching
from hiring_scraper.http import Client

LEVER_BOARD = 'https://jobs.eu.lever.co/engelvoelkers'
LEVER_FEED = 'https://api.eu.lever.co/v0/postings/engelvoelkers?mode=json&limit=100'
RECRUITEE_TENANTS = ('cluetecgmbh', 'nemenergy', 'virtual7jobs')


def recorded_module(revision, name):
    source = subprocess.check_output(['git', 'show', f'{revision}:hiring_scraper/{name}.py'], text=True)
    module = types.ModuleType('recorded_' + name)
    module.__package__ = 'hiring_scraper'
    exec(compile(source, f'recorded_{name}.py', 'exec'), module.__dict__)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discovery_persistence_trial(args):
    """Compare manual discovery, retaining baseline legacy jobs in real SQLite."""
    from unittest.mock import patch
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from hiring_scraper import discovery
    from hiring_scraper.app import discovery_worker
    from hiring_scraper.app.models import Base, Company, DiscoveryRun, Job

    old_discovery = recorded_module(args.baseline, 'discovery')
    old_ats = recorded_module(args.baseline, 'ats')
    old_discovery.identify, old_discovery.parse_feed = old_ats.identify, old_ats.parse_feed
    old_worker = recorded_module(args.baseline, 'app/discovery_worker')
    rows = []
    for name, url in [('cluetec', 'https://cluetec-audit.de/'),
                      ('Balcke Dürr', 'https://nem-energy.com/'),
                      ('virtual7', 'https://virtual7.de/')]:
        seed = {'name': name, 'website': url}
        with tempfile.TemporaryDirectory(prefix='discovery-persistence-trial-') as folder:
            before = old_discovery.discover(seed, Client(Path(folder) / 'before',
                cache_from=args.discovery_cache, offline_only=True), max_pages=6, max_depth=3)
            after = discovery.discover(seed, Client(Path(folder) / 'after',
                cache_from=args.discovery_cache, offline_only=True), max_pages=6, max_depth=3)
            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory.begin() as session:
                company = Company(source='trial', source_id=url, name=name, website_url=url)
                session.add(company)
                session.flush()
                company_id = company.id
            counts = []
            for module, result in [(old_worker, before), (discovery_worker, after)]:
                with factory.begin() as session:
                    run = DiscoveryRun(company_id=company_id, status='running')
                    session.add(run)
                    session.flush()
                    run_id = run.id
                with patch.object(module, 'SessionLocal', factory):
                    module._persist_discovery(company_id, run_id, copy.deepcopy(result))
                with factory() as session:
                    counts.append(len(session.scalars(select(Job).where(Job.is_active.is_(True))).all()))
            rows.append({'company': name, 'website': url,
                         'baseline_discovered': sum(len(board.get('jobs', [])) for board in before['boards']),
                         'candidate_discovered': sum(len(board.get('jobs', [])) for board in after['boards']),
                         'baseline_active': counts[0], 'candidate_active': counts[1]})
            engine.dispose()
    return {'mode': 'Manual/new discovery; scheduled refresh does not migrate saved HTML sources',
            'baseline_revision': args.baseline, 'companies': rows,
            'baseline_active': sum(row['baseline_active'] for row in rows),
            'candidate_active': sum(row['candidate_active'] for row in rows)}


def run(args):
    baseline_ats = recorded_module(args.baseline, 'ats')
    baseline_matching = recorded_module(args.baseline, 'matching')
    result = {'baseline': args.baseline}
    with tempfile.TemporaryDirectory(prefix='production-trial-replay-') as temporary:
        client = Client(temporary, cache_from=args.captures, offline_only=True)
        meta, body = ats.fetch_feed(client, 'lever', LEVER_FEED, LEVER_BOARD)
        if meta.get('state') != 'ok':
            raise ValueError('The Lever source captures are unavailable')
        parsed = meta['parsed_feed']
        result['lever'] = {'baseline_jobs': len(baseline_ats.parse_feed('lever', body, LEVER_BOARD)['jobs']),
                           'candidate_jobs': len(parsed['jobs']),
                           'candidate_unique_ids': len({job['id'] for job in parsed['jobs']}),
                           'complete': parsed['complete'], 'pagination': meta['pagination']}
        feeds = []
        for tenant in RECRUITEE_TENANTS:
            board_url = f'https://{tenant}.recruitee.com'
            provider = ats.identify(board_url)
            if not provider or not provider.get('feed_url'):
                raise ValueError(f'Recruitee support unavailable: {tenant}')
            meta, body = ats.fetch_feed(client, 'recruitee', provider['feed_url'], provider['board_url'])
            if meta.get('state') != 'ok':
                raise ValueError(f'Missing Recruitee capture: {tenant}')
            parsed = meta.get('parsed_feed') or ats.parse_feed('recruitee', body, provider['board_url'])
            old = baseline_ats.identify(board_url)
            feeds.append({'tenant': tenant, 'baseline_feed_url': old.get('feed_url') if old else None,
                          'active_jobs': len(parsed['jobs']), 'unique_ids': len({job['id'] for job in parsed['jobs']}),
                          'complete': parsed['complete'], 'source_sha256': meta['sha256'],
                          'checked_at': meta['checked_at'],
                          'job_ids': sorted(job['id'] for job in parsed['jobs'])})
        result['recruitee'] = feeds
    if args.details:
        rows = json.loads(args.details.read_text())
        recovered = []
        for row in rows:
            path = Path(row['source_meta']).with_suffix('.body')
            body = path.read_bytes()
            if sha(path) != row['meta']['sha256']:
                raise ValueError('Detail capture changed')
            job = row['job']
            before = baseline_matching.detail_updates(job, body, job['url'])
            after = matching.detail_updates(job, body, job['url'])
            recovered.append({'url': job['url'], 'source_sha256': sha(path),
                              'before': bool(before), 'after': bool(after),
                              'existing_update_preserved': before == after if before else None,
                              'description_chars': len(after.get('description', ''))})
        result['details'] = {'pages': len(rows), 'before': sum(row['before'] for row in recovered),
                             'after': sum(row['after'] for row in recovered),
                             'lost': sum(row['before'] and not row['after'] for row in recovered),
                             'existing_updates_preserved': all(row['existing_update_preserved'] for row in recovered if row['before']),
                             'results': recovered}
    if args.provider_corpus:
        urls = json.loads(args.provider_corpus.read_text())
        result['provider_urls'] = {'source_sha256': sha(args.provider_corpus), 'unique_urls': len(set(urls)),
                                  'results': [{'url': url, 'before': baseline_ats.identify(url),
                                               'after': ats.identify(url)} for url in urls]}
        observations = result['provider_urls']['results']
        def identity(value):
            return (value['provider'], value['tenant']) if value else None
        result['provider_urls']['attribution_summary'] = {
            'unchanged': sum(identity(row['before']) == identity(row['after']) for row in observations),
            'rejected': sum(bool(row['before']) and not row['after'] for row in observations),
            'corrected': sum(bool(row['before']) and bool(row['after']) and
                             identity(row['before']) != identity(row['after']) for row in observations),
        }
    if args.discovery_cache:
        result['legacy_discovery_persistence'] = discovery_persistence_trial(args)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='1030d1593c48c979f8bd839817e9f19baa757868')
    parser.add_argument('--captures', required=True, type=Path)
    parser.add_argument('--details', type=Path)
    parser.add_argument('--provider-corpus', type=Path)
    parser.add_argument('--discovery-cache', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key in {'baseline', 'recruitee'}}, indent=2))


if __name__ == '__main__':
    main()
