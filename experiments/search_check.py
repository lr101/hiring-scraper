"""Validate six manually curated search hits; does not automate a search engine."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from probe import probe, UA

base=Path('data/karlsruhe')
results=[]
for i,seed in enumerate(json.loads((base/'search_seeds.json').read_text())):
    url=seed['url']; p=urlsplit(url)
    meta,body=probe(f'{p.scheme}://{p.netloc}/robots.txt',base/'search',f'{i:02d}_robots')
    rp=RobotFileParser(); rp.parse(body.decode('utf-8',errors='replace').splitlines())
    allowed=meta.get('status')==404 or (meta.get('status')==200 and not body.lstrip().startswith(b'<') and rp.can_fetch(UA,url))
    row=dict(seed)
    time.sleep(1)
    if allowed:
        meta,body=probe(url,base/'search',f'{i:02d}_career'); row['status']=meta.get('status'); row['final_url']=meta.get('final_url')
    else:
        row['result']='robots_disallowed_or_unavailable'
    results.append(row)
    (base/'search_results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
    time.sleep(1)
