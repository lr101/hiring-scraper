"""Run an auditable career discovery POC from a JSON list of company homepages."""
import argparse
import csv
import json
import hashlib
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .discovery import discover
from .http import Client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True,help='New run directory; existing results are never overwritten')
    parser.add_argument('--cache-from',type=Path,help='Reuse and copy verified prior HTTP captures; fetch missing URLs live')
    parser.add_argument('--max-pages',type=int,default=6)
    parser.add_argument('--max-depth',type=int,default=3)
    parser.add_argument('--timeout',type=float,default=12)
    parser.add_argument('--delay',type=float,default=1)
    parser.add_argument('--max-requests',type=int,default=300)
    args=parser.parse_args()
    if min(args.max_pages,args.max_depth,args.timeout,args.max_requests)<=0 or args.delay<0:
        parser.error('Limits must be positive; delay must be nonnegative')
    seeds=json.loads(args.seeds.read_text())
    if not isinstance(seeds,list) or not seeds:
        parser.error('Seeds must be a nonempty list')
    if args.out.exists(): parser.error('Output directory exists; choose a new run directory')
    args.out.mkdir(parents=True)
    (args.out/'seeds.json').write_text(json.dumps(seeds,indent=2,ensure_ascii=False)+'\n')
    run={'started_at':datetime.now(timezone.utc).isoformat(),'arguments':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
         'git_revision':subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()}
    run['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    run['git_dirty']=bool(subprocess.run(['git','status','--porcelain'],capture_output=True,text=True).stdout.strip())
    (args.out/'run.json').write_text(json.dumps(run,indent=2)+'\n')
    client=Client(args.out/'http',args.timeout,args.delay,args.max_requests,args.cache_from)
    results=[]
    for seed in seeds:
        result=discover(seed,client,args.max_pages,args.max_depth)
        results.append(result)
        (args.out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps({'name':seed['name'],'status':result['status'],'pages':len(result['pages']),
                          'providers':[(b['provider'],b['feed_state'],b['job_count']) for b in result['boards']]},ensure_ascii=False),flush=True)
    summary={'companies':len(results),'statuses':dict(Counter(r['status'] for r in results)),
             'unique_requests_or_reused_captures':len(client.records),
             'new_requests':sum('reused_from' not in r for r in client.records),
             'reused_captures':sum('reused_from' in r for r in client.records),'http_statuses':dict(Counter(str(r.get('status',r['state'])) for r in client.records)),
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
