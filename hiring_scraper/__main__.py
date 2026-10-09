"""Run an auditable career discovery POC from a JSON list of company homepages."""
import argparse
import csv
import json
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from .discovery import discover
from .http import Client, OriginPacer, RequestBudget
from .provenance import git_metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True,help='New run directory; existing results are never overwritten')
    parser.add_argument('--cache-from',type=Path,help='Reuse and copy verified prior HTTP captures; fetch missing URLs live')
    parser.add_argument('--ignore-robots',action='store_true',help='Skip robots.txt requests for public pages; pacing and request caps still apply')
    parser.add_argument('--offline-only',action='store_true',help='Never make live requests; mark uncached URLs as cache misses')
    parser.add_argument('--max-pages',type=int,default=6)
    parser.add_argument('--max-depth',type=int,default=3)
    parser.add_argument('--timeout',type=float,default=12)
    parser.add_argument('--delay',type=float,default=1)
    parser.add_argument('--max-requests',type=int,default=300)
    parser.add_argument('--workers',type=int,default=1,help='Concurrent company crawls; per-origin pacing and total request budget are shared')
    parser.add_argument('--checkpoint-every',type=int,default=5,help='Persist partial results after this many completed companies')
    args=parser.parse_args()
    if min(args.max_pages,args.max_depth,args.timeout,args.max_requests,args.workers,args.checkpoint_every)<=0 or args.delay<0 or args.workers>16:
        parser.error('Limits must be positive; delay must be nonnegative')
    seeds=json.loads(args.seeds.read_text())
    if not isinstance(seeds,list) or not seeds:
        parser.error('Seeds must be a nonempty list')
    if args.out.exists(): parser.error('Output directory exists; choose a new run directory')
    args.out.mkdir(parents=True)
    (args.out/'seeds.json').write_text(json.dumps(seeds,indent=2,ensure_ascii=False)+'\n')
    run={'started_at':datetime.now(timezone.utc).isoformat(),'arguments':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
         **git_metadata(Path(__file__).resolve().parents[1])}
    run['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    (args.out/'run.json').write_text(json.dumps(run,indent=2)+'\n')
    worker_count=min(args.workers,len(seeds))
    budget=RequestBudget(args.max_requests)
    pacer=OriginPacer()
    results_by_index={}
    result_lock=Lock()
    completed=0

    def persist_partial():
        (args.out/'results.json').write_text(json.dumps([results_by_index[index] for index in sorted(results_by_index)],
                                                        indent=2,ensure_ascii=False)+'\n')

    def crawl_worker(worker_id, indexed_seeds):
        nonlocal completed
        client=Client(args.out/'http'/f'worker-{worker_id}',args.timeout,args.delay,args.max_requests,
                      args.cache_from,origin_pacer=pacer,request_budget=budget,offline_only=args.offline_only,respect_robots=not args.ignore_robots)
        for index,seed in indexed_seeds:
            try:
                result=discover(seed,client,args.max_pages,args.max_depth)
            except Exception as error:
                result={**seed,'pages':[],'boards':[],'status':'crawl_error','error':str(error)[:2000]}
            with result_lock:
                results_by_index[index]=result
                completed+=1
                if completed%args.checkpoint_every==0 or completed==len(seeds):
                    persist_partial()
            print(json.dumps({'name':seed['name'],'status':result['status'],'pages':len(result.get('pages',[])),
                              'providers':[(b['provider'],b['feed_state'],b['job_count']) for b in result.get('boards',[])]},
                             ensure_ascii=False),flush=True)

    work=[[] for _ in range(worker_count)]
    for index,seed in enumerate(seeds):
        work[index%worker_count].append((index,seed))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures=[pool.submit(crawl_worker,worker_id,items) for worker_id,items in enumerate(work)]
        for future in as_completed(futures):
            future.result()
    results=[results_by_index[index] for index in range(len(seeds))]
    persist_partial()

    # Flatten per-worker captures and retain the latest available response for each URL.
    captures_by_url={}
    for metadata_path in sorted((args.out/'http').glob('worker-*/*.json')):
        record=json.loads(metadata_path.read_text(encoding='utf-8'))
        body_path=metadata_path.with_suffix('.body')
        body=body_path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=record.get('sha256'):
            raise ValueError(f'Corrupt HTTP capture: {metadata_path}')
        previous=captures_by_url.get(record['url'])
        current_key=(record.get('checked_at',''), 'reused_from' not in record, metadata_path.as_posix())
        previous_key=((previous[0].get('checked_at',''), 'reused_from' not in previous[0], previous[2].as_posix())
                      if previous else None)
        if previous is None or current_key>previous_key:
            captures_by_url[record['url']]=(record,body,metadata_path)
    flat_http=args.out/'http'
    for record,body,_ in captures_by_url.values():
        (flat_http/(record['capture']+'.body')).write_bytes(body)
        (flat_http/(record['capture']+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    for worker_dir in flat_http.glob('worker-*'):
        shutil.rmtree(worker_dir)

    unique_records=[capture[0] for capture in captures_by_url.values()]
    summary={'companies':len(results),'statuses':dict(Counter(r['status'] for r in results)),
             'unique_requests_or_reused_captures':len(unique_records),
             'new_requests':budget.used,
             'reused_captures':sum('reused_from' in r for r in unique_records),
             'http_statuses':dict(Counter(str(r.get('status',r['state'])) for r in unique_records)),
             'by_cohort':{c:dict(Counter(r['status'] for r in results if r.get('cohort','unspecified')==c)) for c in sorted({r.get('cohort','unspecified') for r in results})},
             'finished_at':datetime.now(timezone.utc).isoformat()}
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (args.out/'companies.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['name','website','cohort','status','career_pages','providers','feed_jobs'],lineterminator='\n')
        writer.writeheader()
        for r in results:
            writer.writerow({'name':r['name'],'website':r['website'],'cohort':r.get('cohort',''), 'status':r['status'],
                             'career_pages':' | '.join(p['url'] for p in r['pages'] if p.get('classification') in {'career_content','jobposting'}),
                             'providers':' | '.join(b['provider'] for b in r['boards']),
                             'feed_jobs':' | '.join(str(b['job_count']) for b in r['boards'] if b['job_count'] is not None)})
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__': main()
