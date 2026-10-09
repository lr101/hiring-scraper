"""Reproduce the frozen 102-employer Bonn discovery benchmark.

Live requests require --live. Default replays use hash-checked local captures.
The committed compact report needs no captures to inspect; bodies stay ignored.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import types

from hiring_scraper import discovery
from hiring_scraper.http import Client, OriginPacer, RequestBudget

ROOT = Path('data/bonn-discovery')
BASELINE = '0615a3c5'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def baseline_module(revision, directory):
    """Load a trusted local Git revision with its own relative parser imports."""
    name = '_bonn_baseline'
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules[name] = package
    for module in ('ats', 'html_jobs', 'pages', 'discovery'):
        source = subprocess.check_output(['git', 'show', f'{revision}:hiring_scraper/{module}.py'], text=True)
        (directory/(module+'.py')).write_text(source)
    return importlib.import_module(name+'.discovery')


def archive_new_captures(directory, cache):
    """Publish new live captures after readers finish; preserve existing evidence."""
    cache.mkdir(parents=True, exist_ok=True)
    for metadata in sorted(directory.glob('*/*.json')):
        target = cache / metadata.name
        if target.exists():
            continue
        record = read(metadata)
        body = metadata.with_suffix('.body').read_bytes()
        if hashlib.sha256(body).hexdigest() != record['sha256']:
            raise ValueError(f'Corrupt new capture: {metadata}')
        target.with_suffix('.body').write_bytes(body)
        target.write_bytes(metadata.read_bytes())


def crawl(args):
    cohort = read(args.root/'cohort.json')
    output = args.root/'raw'/(args.variant+'.json')
    pacer, budget = OriginPacer(), RequestBudget(args.requests)
    completed = {}
    with TemporaryDirectory(prefix='bonn-baseline-') as folder:
        module = discovery if args.variant in {'candidate', 'public', 'public-twelve'} else baseline_module(args.baseline, Path(folder))
        pages = getattr(args, 'pages', None) or (12 if args.variant == 'expanded' else 6)
        # Validate immutable input once; each client gets its own cache mapping.
        captured = Client(Path(folder)/'validated-input', cache_from=args.root/'http', offline_only=True).cache
        def run(pair):
            index, seed = pair
            client = Client(Path(folder)/'captures'/str(index), timeout=10, delay=1, max_requests=45,
                            origin_pacer=pacer, request_budget=budget,
                            offline_only=not args.live, respect_robots=not getattr(args, 'ignore_robots', False))
            client.cache = dict(captured)
            result = module.discover(seed, client, max_pages=pages, max_depth=3)
            result['benchmark_index'] = index
            result['capture_records_used'] = len(client.records)
            result['cache_misses'] = sum(page['fetch_state']=='cache_miss' for page in result['pages'])
            return result
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(run, pair) for pair in enumerate(cohort)]):
                result = future.result()
                completed[result['benchmark_index']] = result
                write(output, [completed[key] for key in sorted(completed)])
                print(len(completed), result['name'], result['status'], flush=True)
        if args.live:
            archive_new_captures(Path(folder)/'captures', args.root/'http')
    write(args.root/'raw'/(args.variant+'-run.json'), {
        'baseline_revision':args.baseline,'variant':args.variant,'live':args.live,
        'max_pages':pages,'max_depth':3,'per_company_request_cap':45,
        'shared_request_cap':args.requests,'new_requests':budget.used,
        'companies':len(completed),'respect_robots':not getattr(args, 'ignore_robots', False),'finished_at':datetime.now(timezone.utc).isoformat()})


def diagnostics(args):
    seeds = [
        {'name':'DHL','website':'https://www.dhl.de/'},
        {'name':'DHL','website':'https://careers.dhl.com/eu/de/'},
        {'name':'DHL','website':'https://careers.dhl.com/eu/de/jobs-in-bonn'},
        {'name':'Deutsche Telekom','website':'https://careers.telekom.com/de/jobs?location=Bonn'},
    ]
    results = []
    with TemporaryDirectory(prefix='bonn-diagnostic-') as folder:
        baseline = baseline_module(args.baseline, Path(folder))
        for index, seed in enumerate(seeds):
            row = {'seed':seed}
            for label, module in [('baseline', baseline),('candidate', discovery)]:
                client = Client(Path(folder)/'captures'/f'{index}-{label}', timeout=15, max_requests=60,
                                cache_from=args.root/'http', offline_only=not args.live, respect_robots=not getattr(args, 'ignore_robots', False))
                row[label] = module.discover(seed, client, max_pages=6, max_depth=3)
            results.append(row)
        if args.live:
            archive_new_captures(Path(folder)/'captures', args.root/'http')
    write(args.root/'raw'/'diagnostics.json', results)


def compact(result):
    jobs = [{**{key:job.get(key) for key in ('id','title','url','location','locations')},
             'employer':(job.get('raw_metadata') or {}).get('hiring_organization'),
             'method':(job.get('raw_metadata') or {}).get('extraction_method')}
            for board in result.get('boards',[]) for job in board.get('jobs',[])]
    return {'status':result['status'],'unverified_external_ats':result.get('unverified_external_ats', []),'pages':[{key:page.get(key) for key in
                ('url','parent','method','fetch_state','http_status','capture','classification','html_extraction_trust')}
                for page in result.get('pages',[])],
            'boards':[{key:board.get(key) for key in
                ('provider','board_url','feed_url','feed_state','job_count','complete','employer_rows_rejected')}
                for board in result.get('boards',[])],
            'limits':result.get('limits'),'job_rows':len(jobs),'jobs':jobs}


def summarize(args):
    variants = {name:read(args.root/'raw'/(name+'.json')) for name in ('baseline','expanded','candidate')}
    cohort = read(args.root/'cohort.json')
    for name, rows in variants.items():
        if len(rows)!=102 or {row['benchmark_index'] for row in rows}!=set(range(102)):
            raise ValueError(f'{name}: expected all 102 frozen rows')
    indexed = {name:{row['benchmark_index']:row for row in rows} for name,rows in variants.items()}
    summary = {}
    for name, rows in variants.items():
        summary[name] = {'companies':len(rows),'statuses':dict(Counter(row['status'] for row in rows)),
            'companies_with_jobs':sum(any(board.get('jobs') for board in row['boards']) for row in rows),
            'job_rows':sum(len(board.get('jobs',[])) for row in rows for board in row['boards']),
            'pages':sum(len(row['pages']) for row in rows),
            'fetch_states':dict(Counter(page['fetch_state'] for row in rows for page in row['pages'])),
            'root_fetch_states':dict(Counter(row['pages'][0]['fetch_state'] for row in rows if row['pages']))}
    companies = [{**{key:seed.get(key) for key in ('name','website','source_url','source_id','category')},
                  **{name:compact(indexed[name][index]) for name in variants}}
                 for index,seed in enumerate(cohort)]
    diag_path=args.root/'raw'/'diagnostics.json'
    diag=[{'seed':row['seed'], **{name:compact(row[name]) for name in ('baseline','candidate')}}
          for row in read(diag_path)] if diag_path.exists() else []
    captures = [read(path) for path in sorted((args.root/'http').glob('*.json'))]
    evidence = [{key:record.get(key) for key in ('url','checked_at','state','status','content_type','bytes','capture','sha256','error')}
                for record in captures]
    artifact={'baseline_revision':args.baseline,'scope':read(args.root/'scope.json'),
        'cohort_sha256':hashlib.sha256((args.root/'cohort.json').read_bytes()).hexdigest(),
        'warning':'Pipeline rows are not market recall or audited active vacancies. Shared group tenant rows and generic HTML headings need attribution/identity auditing. All national/international jobs retain source locations; this is not a Bonn-only job count.',
        'summary':summary,'diagnostics':diag,'companies':companies,'capture_manifest':evidence}
    persistence = args.root/'raw'/'persistence.json'
    if persistence.exists():
        artifact['persistence_trial'] = read(persistence)
    detail_pilot = args.root/'raw'/'telekom-details-pilot.json'
    if detail_pilot.exists():
        artifact['telekom_detail_pilot'] = [{
            'url':row['url'], 'state':row['meta']['state'], 'capture':row['meta'].get('capture'),
            'sha256':row['meta'].get('sha256'), 'search_row':row['row'],
            'structured_postings':[{key:job.get(key) for key in ('id','title','url','location','raw_metadata')}
                                   for job in row['extraction'].get('jobs',[])
                                   if (job.get('raw_metadata') or {}).get('extraction_method')=='schema_org_jobposting'],
        } for row in read(detail_pilot)]
    write(args.output,artifact)
    print(json.dumps(summary,indent=2))


def summarize_public(args):
    """Freeze all paired public-source outcomes with validated capture evidence."""
    cohort = read(args.root/'cohort.json')
    variants = {}
    summary = {}
    for label, filename in [('six_pages', 'candidate'), ('twelve_pages', 'public-twelve'), ('twenty_four_pages', 'public')]:
        rows = read(args.root/'raw'/(filename+'.json'))
        if len(rows) != 102 or {row['benchmark_index'] for row in rows} != set(range(102)):
            raise ValueError(f'{filename}: expected all 102 frozen rows')
        variants[label] = {row['benchmark_index']: row for row in rows}
        summary[label] = {
            'companies': len(rows), 'companies_with_jobs': sum(any(board.get('jobs') for board in row['boards']) for row in rows),
            'job_rows': sum(len(board.get('jobs', [])) for row in rows for board in row['boards']),
            'pages': sum(len(row['pages']) for row in rows), 'statuses': dict(Counter(row['status'] for row in rows)),
            'root_fetch_states': dict(Counter(row['pages'][0]['fetch_state'] for row in rows)),
            'run': read(args.root/'raw'/(filename+'-run.json'))}
    evidence = []
    for path in sorted((args.root/'http').glob('*.json')):
        record = read(path)
        if hashlib.sha256(path.with_suffix('.body').read_bytes()).hexdigest() != record['sha256']:
            raise ValueError(f'Capture hash mismatch: {path}')
        evidence.append({key: record.get(key) for key in ('url', 'checked_at', 'state', 'status', 'content_type', 'bytes', 'capture', 'sha256', 'error')})
    artifact = {'scope': read(args.root/'scope.json'),
        'cohort_sha256': hashlib.sha256((args.root/'cohort.json').read_bytes()).hexdigest(),
        'warning': 'Counts are pipeline rows, not audited vacancies or Bonn-only jobs. Unrelated Reltio/Tucows feeds are excluded; Personio posting links remain scoped. Remaining shared-host GC rows need employer attribution. No anti-bot challenge bypass was performed.',
        'summary': summary, 'companies': [{**{key:seed.get(key) for key in ('name','website','source_url','source_id')},
            **{label:compact(rows[index]) for label, rows in variants.items()}} for index, seed in enumerate(cohort)],
        'capture_manifest': evidence}
    persistence = args.root/'raw'/'persistence.json'
    if persistence.exists():
        artifact['persistence_trial'] = read(persistence)
    write(args.output, artifact)
    print(json.dumps(summary, indent=2))


def persist(args):
    """Exercise real discovery persistence and scheduled refresh in isolated SQLite."""
    from unittest.mock import patch
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from hiring_scraper.app import discovery_worker, worker
    from hiring_scraper.app.models import Base, Company, DiscoveryRun, Job, JobFeed, ScanRun
    from hiring_scraper.ats import fetch_feed
    observations=[]
    for result in read(args.root/'raw'/'diagnostics.json'):
        engine=create_engine('sqlite://')
        Base.metadata.create_all(engine)
        factory=sessionmaker(bind=engine,expire_on_commit=False)
        try:
            seed=result['seed']
            with factory.begin() as session:
                company=Company(source='bonn-benchmark',source_id=seed['website'],name=seed['name'],
                                website_url=seed['website'],latitude=50.7374,longitude=7.0982)
                session.add(company);session.flush();company_id=company.id
            counts={}
            for name in ('baseline','candidate'):
                with factory.begin() as session:
                    run=DiscoveryRun(company_id=company_id,status='running')
                    session.add(run);session.flush();run_id=run.id
                with patch.object(discovery_worker,'SessionLocal',factory):
                    discovery_worker._persist_discovery(company_id,run_id,result[name])
                with factory() as session:
                    counts[name]=len(session.scalars(select(Job).where(Job.is_active.is_(True))).all())
            with factory() as session:
                feeds=session.scalars(select(JobFeed).where(JobFeed.job_count>0)).all()
                sources=[(feed.id,feed.provider,feed.feed_url,feed.board_url) for feed in feeds]
            refreshed=[]
            for feed_id,provider,url,board in sources:
                with factory.begin() as session:
                    run=ScanRun(feed_id=feed_id,status='running')
                    session.add(run);session.flush();run_id=run.id
                with TemporaryDirectory(prefix='bonn-refresh-') as folder:
                    client=Client(folder,cache_from=args.root/'http',offline_only=True, respect_robots=not getattr(args, 'ignore_robots', False))
                    meta,body=fetch_feed(client,provider,url,board)
                if meta.get('state')!='ok':
                    raise ValueError(f'Missing refresh capture: {url}')
                with patch.object(worker,'SessionLocal',factory):
                    worker._persist_result(feed_id,run_id,meta,body)
                with factory() as session:
                    feed=session.get(JobFeed,feed_id)
                    refreshed.append({'provider':provider,'feed_url':url,'status':feed.status})
            with factory() as session:
                counts['after_refresh']=len(session.scalars(select(Job).where(Job.is_active.is_(True))).all())
            observations.append({'seed':seed,'active_counts':counts,'refreshed_sources':refreshed})
        finally:engine.dispose()
    write(args.root/'raw'/'persistence.json',observations)
    print(json.dumps(observations,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['crawl','diagnostics','persist','summarize','summarize-public'])
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--baseline',default=BASELINE)
    parser.add_argument('--variant',choices=['baseline','expanded','candidate','public','public-twelve'],default='candidate')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--ignore-robots',action='store_true')
    parser.add_argument('--pages',type=int,choices=range(1,25))
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--requests',type=int,default=2500)
    parser.add_argument('--output',type=Path,default=Path('reports/bonn-discovery-benchmark.json'))
    args=parser.parse_args()
    {'crawl':crawl,'diagnostics':diagnostics,'persist':persist,'summarize':summarize,'summarize-public':summarize_public}[args.action](args)

if __name__=='__main__':main()
